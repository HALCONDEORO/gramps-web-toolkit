"""Keep homonym decisions in small helpers, outside the whole-tree checker.

This stdlib AST decision count is a local structural regression, not the
DeepSource metric. It includes nested functions so moving code into a closure
inside run_checks cannot hide the added decisions.
"""
import ast
import inspect

from gwt_check import checks


def decision_complexity(node):
    return 1 + sum(
        int(isinstance(n, (ast.If, ast.IfExp, ast.For, ast.AsyncFor, ast.While,
                           ast.ExceptHandler, ast.Assert)))
        + (len(n.values) - 1 if isinstance(n, ast.BoolOp) else 0)
        + (1 + len(n.ifs) if isinstance(n, ast.comprehension) else 0)
        for n in ast.walk(node)
    )


def test_homonym_complexity_stays_outside_run_checks():
    module = ast.parse(inspect.getsource(checks))
    run_checks = next(n for n in module.body if isinstance(n, ast.FunctionDef) and n.name == 'run_checks')
    assert decision_complexity(run_checks) <= 401
    helpers = [n for n in module.body if isinstance(n, ast.FunctionDef) and n.name.startswith('_homonym_')]
    accumulator = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == '_HomonymBirthGaps')
    helpers += [n for n in accumulator.body if isinstance(n, ast.FunctionDef)]
    assert helpers
    assert all(decision_complexity(n) <= 12 for n in helpers)
