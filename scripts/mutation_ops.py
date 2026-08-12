"""
Mutation operators for PydanticBench task generation.

Each operator locates syntactic sites in a module and can rewrite exactly ONE of
them, chosen by index. libcst is used rather than `ast` because it preserves
formatting byte-for-byte, so the resulting diff contains only the semantic edit
and nothing else. That matters: a mutation that also reflowed whitespace would
be trivially visible to an agent running `git diff`.

Design: a single transformer class runs in two modes.
  target=None  -> "count" mode; traverses and counts eligible sites, changes nothing
  target=k     -> "apply" mode; rewrites the k-th eligible site only
libcst traversal is deterministic, so indices agree between the two passes.
"""

from __future__ import annotations

import libcst as cst


class SingleSiteOperator(cst.CSTTransformer):
    """Base class. Subclasses call ``self._hit`` from their ``leave_*`` methods."""

    name = "base"
    description = "base operator"

    def __init__(self, target: int | None = None):
        super().__init__()
        self.count = 0
        self.target = target
        self.applied: dict | None = None

    def _hit(self, updated, produce, before: str, after: str):
        idx = self.count
        self.count += 1
        if self.target is not None and idx == self.target:
            self.applied = {"operator": self.name, "site_index": idx,
                            "before": before, "after": after}
            return produce()
        return updated


# Comparison operators: classic off-by-one / boundary bugs. Highest-value
# operator in practice -- they break exactly the boundary cases, which lands
# squarely in the 1-4 failure band we accept.
_CMP_SWAP = {
    cst.LessThan: cst.LessThanEqual, cst.LessThanEqual: cst.LessThan,
    cst.GreaterThan: cst.GreaterThanEqual, cst.GreaterThanEqual: cst.GreaterThan,
    cst.Equal: cst.NotEqual, cst.NotEqual: cst.Equal,
    cst.Is: cst.IsNot, cst.IsNot: cst.Is,
    cst.In: cst.NotIn, cst.NotIn: cst.In,
}


class CompareOpSwap(SingleSiteOperator):
    name = "compare_op_swap"
    description = "swap a comparison operator (boundary / off-by-one bug)"

    def leave_ComparisonTarget(self, original, updated):
        repl = _CMP_SWAP.get(type(updated.operator))
        if repl is None:
            return updated
        return self._hit(updated, lambda: updated.with_changes(operator=repl()),
                         type(updated.operator).__name__, repl.__name__)


class BoolOpSwap(SingleSiteOperator):
    name = "bool_op_swap"
    description = "swap `and` with `or` in a boolean expression"

    def leave_BooleanOperation(self, original, updated):
        if isinstance(updated.operator, cst.And):
            new_op = cst.Or
        elif isinstance(updated.operator, cst.Or):
            new_op = cst.And
        else:
            return updated
        op = updated.operator
        return self._hit(
            updated,
            lambda: updated.with_changes(operator=new_op(
                whitespace_before=op.whitespace_before,
                whitespace_after=op.whitespace_after)),
            type(op).__name__, new_op.__name__)


class NotRemoval(SingleSiteOperator):
    name = "not_removal"
    description = "remove a logical negation, inverting a guard condition"

    def leave_UnaryOperation(self, original, updated):
        if not isinstance(updated.operator, cst.Not):
            return updated
        return self._hit(updated, lambda: updated.expression, "not X", "X")


class BoolLiteralFlip(SingleSiteOperator):
    name = "bool_literal_flip"
    description = "flip a True/False literal"

    def leave_Name(self, original, updated):
        if updated.value == "True":
            new = "False"
        elif updated.value == "False":
            new = "True"
        else:
            return updated
        return self._hit(updated, lambda: updated.with_changes(value=new),
                         updated.value, new)


class IntPerturb(SingleSiteOperator):
    name = "int_perturb"
    description = "perturb an integer literal by one (off-by-one)"

    def leave_Integer(self, original, updated):
        try:
            val = int(updated.value, 0)
        except ValueError:
            return updated
        if abs(val) > 64:  # skip magic constants, hashes, byte sizes
            return updated
        new = str(val + 1)
        return self._hit(updated, lambda: updated.with_changes(value=new),
                         updated.value, new)


_BIN_SWAP = {cst.Add: cst.Subtract, cst.Subtract: cst.Add}


class BinOpSwap(SingleSiteOperator):
    name = "bin_op_swap"
    description = "swap + and - in an arithmetic expression"

    def leave_BinaryOperation(self, original, updated):
        repl = _BIN_SWAP.get(type(updated.operator))
        if repl is None:
            return updated
        op = updated.operator
        return self._hit(
            updated,
            lambda: updated.with_changes(operator=repl(
                whitespace_before=op.whitespace_before,
                whitespace_after=op.whitespace_after)),
            type(op).__name__, repl.__name__)


# Tests asserting on exception TYPE catch this; tests asserting only "raises"
# do not -- good specificity.
_EXC_SWAP = {"ValueError": "TypeError", "TypeError": "ValueError",
             "KeyError": "AttributeError", "AttributeError": "KeyError"}


class ExceptionSwap(SingleSiteOperator):
    name = "exception_swap"
    description = "raise a different exception type"

    def leave_Raise(self, original, updated):
        exc = updated.exc
        func = None
        if isinstance(exc, cst.Call) and isinstance(exc.func, cst.Name):
            func = exc.func
        elif isinstance(exc, cst.Name):
            func = exc
        if func is None or func.value not in _EXC_SWAP:
            return updated
        new_name = _EXC_SWAP[func.value]

        def produce():
            if isinstance(exc, cst.Call):
                return updated.with_changes(
                    exc=exc.with_changes(func=func.with_changes(value=new_name)))
            return updated.with_changes(exc=func.with_changes(value=new_name))

        return self._hit(updated, produce, func.value, new_name)


class BranchSwap(SingleSiteOperator):
    name = "branch_swap"
    description = "exchange the bodies of an if/else"

    def leave_If(self, original, updated):
        orelse = updated.orelse
        if not isinstance(orelse, cst.Else):
            return updated
        return self._hit(
            updated,
            lambda: updated.with_changes(body=orelse.body,
                                         orelse=orelse.with_changes(body=updated.body)),
            "if A else B", "if B else A")


class KeywordDefaultFlip(SingleSiteOperator):
    name = "kwarg_flip"
    description = "flip a boolean keyword argument at a call site"

    def leave_Arg(self, original, updated):
        if updated.keyword is None:
            return updated
        v = updated.value
        if not isinstance(v, cst.Name) or v.value not in ("True", "False"):
            return updated
        new = "False" if v.value == "True" else "True"
        return self._hit(
            updated, lambda: updated.with_changes(value=v.with_changes(value=new)),
            f"{updated.keyword.value}={v.value}", f"{updated.keyword.value}={new}")


ALL_OPERATORS: list[type[SingleSiteOperator]] = [
    CompareOpSwap, BoolOpSwap, NotRemoval, BoolLiteralFlip, IntPerturb,
    BinOpSwap, ExceptionSwap, BranchSwap, KeywordDefaultFlip,
]
OPERATORS_BY_NAME = {op.name: op for op in ALL_OPERATORS}


def count_sites(op_cls: type[SingleSiteOperator], module: cst.Module) -> int:
    t = op_cls(target=None)
    module.visit(t)
    return t.count


def apply_site(op_cls: type[SingleSiteOperator], module: cst.Module, index: int):
    """Return (mutated_source, site_info) or (None, None) if index out of range."""
    t = op_cls(target=index)
    new_module = module.visit(t)
    if t.applied is None:
        return None, None
    return new_module.code, t.applied
