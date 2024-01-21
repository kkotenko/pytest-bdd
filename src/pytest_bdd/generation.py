"""pytest-bdd missing test code generation."""
from __future__ import annotations

import itertools
import os.path
from typing import TYPE_CHECKING, cast

from _pytest._io import TerminalWriter
from mako.lookup import TemplateLookup

from .compat import getfixturedefs
from .feature import get_features
from .scenario import inject_fixturedefs_for_step, make_python_docstring, make_python_name, make_string_literal
from .steps import get_step_fixture_name
from .types import STEP_TYPES

if TYPE_CHECKING:
    from typing import Any, Sequence

    from _pytest.config import Config
    from _pytest.config.argparsing import Parser
    from _pytest.fixtures import FixtureDef, FixtureManager
    from _pytest.main import Session
    from _pytest.python import Function

    from .parser import Feature, ScenarioTemplate, Step

template_lookup = TemplateLookup(directories=[os.path.join(os.path.dirname(__file__), "templates")])


def add_options(parser: Parser) -> None:
    """Add pytest-bdd options."""
    group = parser.getgroup("bdd", "Generation")

    group._addoption(
        "--generate-missing",
        action="store_true",
        dest="generate_missing",
        default=False,
        help="Generate missing bdd test code for given feature files and exit.",
    )

    group._addoption(
        "--feature",
        metavar="FILE_OR_DIR",
        action="append",
        dest="features",
        help="Feature file or directory to generate missing code for. Multiple allowed.",
    )


def cmdline_main(config: Config) -> int | None:
    """Check config option to show missing code."""
    if config.option.generate_missing:
        return show_missing_code(config)
    return None  # Make mypy happy


def generate_code(features: list[Feature], scenarios: list[ScenarioTemplate], steps: list[Step]) -> str:
    """Generate test code for the given filenames."""
    grouped_steps = group_steps(steps)
    template = template_lookup.get_template("test.py.mak")
    code = template.render(
        features=features,
        scenarios=scenarios,
        steps=grouped_steps,
        make_python_name=make_python_name,
        make_python_docstring=make_python_docstring,
        make_string_literal=make_string_literal,
    )
    return cast(str, code)


def show_missing_code(config: Config) -> int:
    """Wrap pytest session to show missing code."""
    from _pytest.main import wrap_session

    return wrap_session(config, _show_missing_code_main)


def print_missing_code(scenarios: list[ScenarioTemplate], steps: list[Step]) -> None:
    """Print missing code with TerminalWriter."""
    tw = TerminalWriter()
    scenario = step = None

    for scenario in scenarios:
        tw.line()
        tw.line(
            'Scenario "{scenario.name}" is not bound to any test in the feature "{scenario.feature.name}"'
            " in the file {scenario.feature.filename}:{scenario.line_number}".format(scenario=scenario),
            red=True,
        )

    if scenario:
        tw.sep("-", red=True)

    for step in steps:
        tw.line()
        if step.scenario is not None:
            tw.line(
                """Step {step} is not defined in the scenario "{step.scenario.name}" in the feature"""
                """ "{step.scenario.feature.name}" in the file"""
                """ {step.scenario.feature.filename}:{step.line_number}""".format(step=step),
                red=True,
            )
        elif step.background is not None:
            tw.line(
                """Step {step} is not defined in the background of the feature"""
                """ "{step.background.feature.name}" in the file"""
                """ {step.background.feature.filename}:{step.line_number}""".format(step=step),
                red=True,
            )

    if step:
        tw.sep("-", red=True)

    tw.line("Please place the code above to the test file(s):")
    tw.line()

    features = sorted(
        (scenario.feature for scenario in scenarios), key=lambda feature: feature.name or feature.filename
    )
    code = generate_code(features, scenarios, steps)
    tw.write(code)


def _find_step_fixturedef(
    fixturemanager: FixtureManager, item: Function, step: Step
) -> Sequence[FixtureDef[Any]] | None:
    """Find step fixturedef."""
    with inject_fixturedefs_for_step(step=step, fixturemanager=fixturemanager, node=item):
        bdd_name = get_step_fixture_name(step=step)
        return getfixturedefs(fixturemanager, bdd_name, item)


def parse_feature_files(paths: list[str], **kwargs: Any) -> tuple[list[Feature], list[ScenarioTemplate], list[Step]]:
    """Parse feature files of given paths.

    :param paths: `list` of paths (file or dirs)

    :return: `list` of `tuple` in form:
             (`list` of `Feature` objects, `list` of `Scenario` objects, `list` of `Step` objects).
    """
    features = get_features(paths, **kwargs)
    scenarios = sorted(
        itertools.chain.from_iterable(feature.scenarios.values() for feature in features),
        key=lambda scenario: (scenario.feature.name or scenario.feature.filename, scenario.name),
    )
    steps = sorted((step for scenario in scenarios for step in scenario.steps), key=lambda step: step.name)
    return features, scenarios, steps


def group_steps(steps: list[Step]) -> list[Step]:
    """Group steps by type."""
    steps = sorted(steps, key=lambda step: step.type)
    seen_steps = set()
    grouped_steps = []
    for step in itertools.chain.from_iterable(
        sorted(group, key=lambda step: step.name) for _, group in itertools.groupby(steps, lambda step: step.type)
    ):
        if step.name not in seen_steps:
            grouped_steps.append(step)
            seen_steps.add(step.name)
    grouped_steps.sort(key=lambda step: STEP_TYPES.index(step.type))
    return grouped_steps


def _show_missing_code_main(config: Config, session: Session) -> None:
    """Preparing fixture duplicates for output."""
    tw = TerminalWriter()
    session.perform_collect()

    fm = session._fixturemanager

    if config.option.features is None:
        tw.line("The --feature parameter is required.", red=True)
        session.exitstatus = 100
        return

    features, scenarios, steps = parse_feature_files(config.option.features)

    for item in session.items:
        if scenario := getattr(item.obj, "__scenario__", None):
            if scenario in scenarios:
                scenarios.remove(scenario)
            for step in scenario.steps:
                if _find_step_fixturedef(fm, item, step=step):
                    try:
                        steps.remove(step)
                    except ValueError:
                        pass
    for scenario in scenarios:
        for step in scenario.steps:
            if step.background is None:
                steps.remove(step)
    grouped_steps = group_steps(steps)
    print_missing_code(scenarios, grouped_steps)

    if scenarios or steps:
        session.exitstatus = 100
