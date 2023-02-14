"""
Transformations regarding linearization of constraints.

Linearized constraints have one of the following forms:


Linear comparison:
------------------
- LinExpr == Constant
- LinExpr >= Constant
- LinExpr <= Constant

    LinExpr can be any of:
        - NumVar
        - sum
        - wsum

Indicator constraints:
----------------------
- BoolVar -> LinExpr == Constant
- BoolVar -> LinExpr >= Constant
- BoolVar -> LinExpr <= Constant

- BoolVar -> GenExpr                    (GenExpr.name in supported, GenExpr.is_bool())
- BoolVar -> GenExpr >= Var/Constant    (GenExpr.name in supported)
- BoolVar -> GenExpr <= Var/Constant    (GenExpr.name in supported)
- BoolVar -> GenExpr == Var/Constant    (GenExpr.name in supported)

Where BoolVar is a boolean variable or its negation.

General comparisons or expressions
-----------------------------------
- GenExpr                               (GenExpr.name in supported, GenExpr.is_bool())
- GenExpr == Var/Constant               (GenExpr.name in supported)
- GenExpr <= Var/Constant               (GenExpr.name in supported)
- GenExpr >= Var/Constant               (GenExpr.name in supported)


"""
import copy
import numpy as np

from .flatten_model import flatten_constraint, get_or_make_var

from ..expressions.core import Comparison, Operator, _wsum_should, _wsum_make
from ..expressions.globalconstraints import GlobalConstraint
from ..expressions.utils import is_any_list, is_num, eval_comparison, is_bool

from ..expressions.variables import _BoolVarImpl, boolvar, NegBoolView, _NumVarImpl

def linearize_constraint(cpm_expr, supported={"sum","wsum"}, reified=False):
    """
    Transforms all constraints to a linear form.
    This function assumes all constraints are in 'flat normal form', and implications only contain boolean variables on the lhs.
    Only apply after 'cpmpy.transformations.flatten_model.flatten_constraint() and cpmpy.transformations.reification.only_bv_implies()'.
    """

    if is_any_list(cpm_expr):
        lin_cons = [linearize_constraint(expr, supported=supported, reified=reified) for expr in cpm_expr]
        return [c for l in lin_cons for c in l]

    # boolvar
    if isinstance(cpm_expr, _BoolVarImpl):
        if isinstance(cpm_expr, NegBoolView):
            return [sum([cpm_expr._bv]) <= 0]
        return [sum([cpm_expr]) >= 1]

    # conjunction
    if cpm_expr.name == "and":
        return [sum(cpm_expr.args) >= len(cpm_expr.args)]

    # disjunction
    if cpm_expr.name == "or":
        return [sum(cpm_expr.args) >= 1]

    # reification
    if cpm_expr.name == "->":
        # determine direction of implication
        cond, sub_expr = cpm_expr.args
        assert isinstance(cond, _BoolVarImpl), f"Linearization of {cpm_expr} is not supported, lhs of implication must be boolvar. Apply `only_bv_implies` before calling `linearize_constraint`"

        if isinstance(cond, _BoolVarImpl) and isinstance(sub_expr, _BoolVarImpl):
            # shortcut for BV -> BV, convert to disjunction and apply linearize on it
            return linearize_constraint(~cond | sub_expr, reified=reified)

        # BV -> LinExpr
        if isinstance(cond, _BoolVarImpl):
            lin_sub = linearize_constraint(sub_expr, supported=supported, reified=True)
            return [cond.implies(lin) for lin in lin_sub]

    # comparisons
    if isinstance(cpm_expr, Comparison):
        lhs, rhs = cpm_expr.args

        # linearize unsupported operators
        if lhs.name not in supported: # TODO: add mul, (abs?), (mod?), (pow?)
            if isinstance(lhs, _NumVarImpl) and is_num(rhs):
                pass
            elif isinstance(lhs, _NumVarImpl) and isinstance(rhs, _NumVarImpl):
                # bring numvar to lhs
                lhs = lhs + -1 * rhs
                rhs = 0

            elif lhs.name == "sub":
                # convert to wsum
                lhs = sum([1 * lhs.args[0] + -1 * lhs.args[1]])

            elif lhs.name == "element":
                arr, idx = cpm_expr.args[0].args
                # Assuming 1-d array
                assert len(arr.shape) == 1, f"Only support 1-d element constraints, not {cpm_expr} which has shape {cpm_expr.shape}"

                n = len(arr)
                sigma = boolvar(shape=n)

                constraints = [sum(sigma) == 1]
                constraints += [sum(np.arange(n) * sigma) == idx]
                # translation with implication:
                constraints += [s.implies(Comparison(cpm_expr.name, a, cpm_expr.args[1])) for s, a in zip(sigma, arr)]
                return linearize_constraint(constraints, supported=supported, reified=reified)

            # other global constraints
            elif isinstance(lhs, GlobalConstraint) and hasattr(lhs, "decompose_comparison"):
                decomp = lhs.decompose_comparison(cpm_expr.name, rhs)
                return linearize_constraint(flatten_constraint(decomp), supported=supported, reified=reified)

            else:
                raise NotImplementedError(f"lhs of constraint {cpm_expr} cannot be linearized, should be any of {supported} or 'sub', 'mul','element' but is {lhs}. Please report on github")

        if isinstance(lhs, Operator) and lhs.name in {"sum","wsum"}:
            # bring all vars to lhs
            if isinstance(rhs, _NumVarImpl):
                if lhs.name == "sum":
                    lhs, rhs = sum([-1 * rhs]+[1 * a for a in lhs.args]), 0
                else:
                    lhs, rhs = lhs + -1*rhs, 0
            # bring all const to rhs
            if lhs.name == "sum":
                rhs += -sum(arg for arg in lhs.args if is_num(arg))
                lhs = sum(arg for arg in lhs.args if not is_num(arg)) # TODO: avoid looping again and keep idxes?
            elif lhs.name == "wsum":
                rhs += -sum(w * arg for w,arg in zip(*lhs.args) if is_num(arg))
                lhs = sum(w * arg for w, arg in zip(*lhs.args) if not is_num(arg))

        if isinstance(lhs, Operator) and lhs.name == "mul" and is_num(lhs.args[0]):
            # convert to wsum
            lhs = Operator("wsum",[[lhs.args[0]],[lhs.args[1]]])

        # now fix the comparisons themselves
        if cpm_expr.name == "<":
            new_rhs, cons = get_or_make_var(rhs - 1)
            return [lhs <= new_rhs] + cons
        if cpm_expr.name == ">":
            new_rhs, cons = get_or_make_var(rhs + 1)
            return [lhs >= new_rhs] + cons
        if cpm_expr.name == "!=":
            # Special case: BV != BV
            if isinstance(lhs, _BoolVarImpl) and isinstance(rhs, _BoolVarImpl):
                return [lhs + rhs == 1]

            if reified or (lhs.name not in {"sum","wsum"} and not isinstance(lhs, _NumVarImpl)):
                # Big M implementation
                z = boolvar()
                # Calculate bounds of M = |lhs - rhs| + 1,  TODO: should be easier after fixing issue #96
                bound1, _ = get_or_make_var(1 + lhs - rhs)
                bound2, _ = get_or_make_var(1 + rhs - lhs)
                M = max(bound1.ub, bound2.ub)

                cons = [lhs - M * z <= rhs - 1, lhs + M * z >= rhs + M + 1]
                return linearize_constraint(flatten_constraint(cons), supported=supported, reified=reified)

            else:
                # introduce new indicator constraints
                z = boolvar()
                constraints = [z.implies(lhs < rhs), (~z).implies(lhs > rhs)]
                return linearize_constraint(constraints, supported=supported, reified=reified)


        return [Comparison(cpm_expr.name, lhs, rhs)]

    elif cpm_expr.name == "alldifferent":
        """
            More efficient implementations possible
            http://yetanothermathprogrammingconsultant.blogspot.com/2016/05/all-different-and-mixed-integer.html
            This method avoids bounds computation
            Introduces n^2 new boolean variables
        """
        # TODO check performance of implementation
        # Boolean variables
        lb, ub = min(arg.lb for arg in cpm_expr.args), max(arg.ub for arg in cpm_expr.args)
        # Linear decomposition of alldifferent using bipartite matching
        sigma = boolvar(shape=(len(cpm_expr.args), 1 + ub - lb))

        constraints = [sum(row) == 1 for row in sigma]  # Exactly one value
        constraints += [sum(col) <= 1 for col in sigma.T]  # All diff values

        for arg, row in zip(cpm_expr.args, sigma):
            constraints += [sum(np.arange(lb, ub + 1) * row) == arg]

        return constraints

    return [cpm_expr]


def only_positive_bv(cpm_expr):
    """
        Replaces constraints containing NegBoolView with equivalent expression using only BoolVar.
        cpm_expr is expected to be linearized. Only apply after applying linearize_constraint(cpm_expr)

        Resulting expression is linear.
    """
    if is_any_list(cpm_expr):
        nn_cons = [only_positive_bv(expr) for expr in cpm_expr]
        return [c for l in nn_cons for c in l]

    if isinstance(cpm_expr, Comparison):
        lhs, rhs = cpm_expr.args
        new_cons = []

        if isinstance(lhs, _NumVarImpl):
            if isinstance(lhs,NegBoolView):
                lhs, rhs = -lhs._bv, rhs - 1

        if lhs.name == "sum" and any(isinstance(a, NegBoolView) for a in lhs.args):
            lhs = Operator("wsum",[[1]*len(lhs.args), lhs.args])

        if lhs.name == "wsum":
            weights, args = lhs.args
            idxes = {i for i, a in enumerate(args) if isinstance(a, NegBoolView)}
            nw, na = zip(*[(-w,a._bv) if i in idxes else (w,a) for i, (w,a) in enumerate(zip(weights, args))])
            lhs = Operator("wsum", [nw, na]) # force making wsum, even for arity = 1
            rhs -= sum(weights[i] for i in idxes)

        if isinstance(lhs, Operator) and lhs.name not in {"sum","wsum"}:
        # other operators in comparison such as "min", "max"
            lhs = copy.copy(lhs)
            for i,arg in enumerate(list(lhs.args)):
                if isinstance(arg, NegBoolView):
                    new_arg, cons = get_or_make_var(1 - arg)
                    lhs.args[i] = new_arg
                    new_cons += cons

        return [Comparison(cpm_expr.name, lhs, rhs)] + new_cons

    # reification
    if cpm_expr.name == "->":
        cond, subexpr = cpm_expr.args
        assert isinstance(cond, _BoolVarImpl), f"{cpm_expr} is not a supported linear expression. Apply `linearize_constraint` before calling `only_positive_bv`"
        if isinstance(cond, _BoolVarImpl): # BV -> Expr
            subexpr = only_positive_bv(subexpr)
            return[cond.implies(expr) for expr in subexpr]

    if isinstance(cpm_expr, GlobalConstraint):
        return [cpm_expr]

    raise Exception(f"{cpm_expr} is not linear or is not supported. Please report on github")





































