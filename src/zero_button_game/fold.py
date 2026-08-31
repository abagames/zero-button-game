"""Paper folding ("fold to target") puzzle logic.

A rectangular sheet of squared paper starts flat. Some of its cells are
coloured. A move folds the sheet along one grid line: the smaller (or equal)
side is flipped over onto the larger side, so the sheet stays rectangular and
never grows. The goal is a dashed target rectangle drawn on the board: fold
until the sheet's outline *is* that rectangle and every cell inside it is
covered by *exactly one* coloured cell in its layer stack. Coloured cells may
never stack on each other, so a solvable sheet always carries exactly as many
coloured cells as the target has cells - the count is readable off the still
frame, which is the whole point of forbidding overlap.

Two facts shape the whole module.

*Folds on different axes commute.* A vertical crease only ever rewrites the x
coordinate and a horizontal crease only the y coordinate, and the legality of a
crease on one axis depends only on that axis' extent. Two fold sequences are
therefore the same answer exactly when they have the same subsequence of
vertical folds and the same subsequence of horizontal folds; every interleaving
is legal and reaches the same sheet. Uniqueness is judged over those classes,
never over raw sequences - judging raw sequences would reject every puzzle that
uses both axes, which is all the interesting ones. The canonical representative
is "all vertical folds, then all horizontal folds".

*The goal only reads the projection.* Whether the target ends up covered
exactly once depends on where the coloured cells land, not on which layer they
land in. Folding maps the whole sheet into the final extent, so every coloured
cell lands somewhere inside the target; "every target cell covered at least
once" plus "coloured cell count == target cell count" is therefore the same
statement as "every target cell covered exactly once". The search can keep
carrying a 36-bit mask instead of a layer stack, which makes
complete enumeration - every fold class of every length - cheap enough to run
per candidate. So the uniqueness claim carries no hidden "within k folds"
caveat: the sheet strictly shrinks on every fold, so the class space is finite
and the solver enumerates all of it.

Layer stacks are still built, because the presentation has to draw them and
because the layer depth is a difficulty signal; ``fold_state`` is the exact
simulation and ``FoldSolver`` is the fast projection of it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Any, Iterable

from .core import StableRng, canonical_json_bytes, sha256_value
from .models import Action, Solution

Cell = tuple[int, int]
Extent = tuple[int, int, int, int]
Fold = tuple[int, int, int]  # (axis, line, direction)
Layer = tuple[int, ...]

FOLD_RULESET = "fold-to-target-exact-v1"
FOLD_EQUIVALENCE_POLICY = (
    "fold sequences modulo commutation of folds on different axes; the canonical "
    "representative folds every vertical crease first, then every horizontal crease"
)
FOLD_EQUIVALENCE_VERSION = "fold-crease-class-v1"

PAPER_WIDTH = 6
PAPER_HEIGHT = 6
MIN_FOLD_COUNT = 2  # the neutrality perturbation reorders the two axis groups
MIN_TARGET_SIDE = 2

# Layer cell values.
NO_PAPER = 0
BLANK = 1
COLOURED = 2


class FoldSolveRejected(RuntimeError):
    def __init__(self, code: str, diagnostics: dict | None = None):
        super().__init__(code)
        self.code = code
        self.diagnostics = diagnostics or {}


# --------------------------------------------------------------------------
# Crease geometry
# --------------------------------------------------------------------------


def fold_result_extent(lo: int, hi: int, line: int, direction: int) -> tuple[int, int]:
    """The 1-D extent left by folding ``[lo, hi)`` at ``line``.

    ``direction == -1`` moves the low side onto the high side, ``+1`` the high
    side onto the low side. The moving side may never be the larger one, which
    is what keeps the sheet rectangular and monotonically shrinking.
    """
    if not lo < line < hi:
        raise ValueError("crease is not strictly inside the extent")
    if direction == -1:
        if line - lo > hi - line:
            raise ValueError("moving side is larger than the side it folds onto")
        return line, hi
    if direction == 1:
        if hi - line > line - lo:
            raise ValueError("moving side is larger than the side it folds onto")
        return lo, line
    raise ValueError("fold direction must be -1 or +1")


def axis_folds(lo: int, hi: int) -> tuple[tuple[int, int], ...]:
    """Every legal ``(line, direction)`` crease inside ``[lo, hi)``."""
    found = []
    for line in range(lo + 1, hi):
        if line - lo <= hi - line:
            found.append((line, -1))
        if hi - line <= line - lo:
            found.append((line, 1))
    return tuple(found)


def legal_folds(extent: Extent) -> tuple[Fold, ...]:
    x0, y0, x1, y1 = extent
    return tuple(
        (axis, line, direction)
        for axis, (lo, hi) in ((0, (x0, x1)), (1, (y0, y1)))
        for line, direction in axis_folds(lo, hi)
    )


_CHAIN_CACHE: dict[tuple[int, int], tuple[tuple[tuple[tuple[int, int], ...], tuple[int, int]], ...]] = {}


def axis_chains(lo: int, hi: int) -> tuple[tuple[tuple[tuple[int, int], ...], tuple[int, int]], ...]:
    """Every fold chain along one axis, including the empty chain.

    Finite because each fold strictly shrinks the extent and a width-1 extent
    has no crease.
    """
    key = (lo, hi)
    cached = _CHAIN_CACHE.get(key)
    if cached is not None:
        return cached
    out = [((), (lo, hi))]
    frontier = [((), (lo, hi))]
    while frontier:
        following = []
        for chain, (a, b) in frontier:
            for line, direction in axis_folds(a, b):
                following.append((chain + ((line, direction),), fold_result_extent(a, b, line, direction)))
        out.extend(following)
        frontier = following
    result = tuple(out)
    _CHAIN_CACHE[key] = result
    return result


def chain_coordinate_map(lo: int, hi: int, chain: Iterable[tuple[int, int]]) -> tuple[dict[int, int], tuple[int, int]]:
    """Where each coordinate of ``[lo, hi)`` ends up after ``chain``."""
    mapping = {c: c for c in range(lo, hi)}
    a, b = lo, hi
    for line, direction in chain:
        a, b = fold_result_extent(a, b, line, direction)
        for source, current in mapping.items():
            if not a <= current < b:
                mapping[source] = 2 * line - 1 - current
    return mapping, (a, b)


# --------------------------------------------------------------------------
# Problem / state
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FoldPuzzleSpec:
    schema_version: str
    puzzle_type: str
    generator_version: str
    width: int
    height: int
    filled: tuple[int, ...]
    target: Extent
    ruleset: str = FOLD_RULESET

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["filled"] = list(self.filled)
        value["target"] = list(self.target)
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "FoldPuzzleSpec":
        return cls(
            value["schema_version"], value["puzzle_type"], value["generator_version"],
            int(value["width"]), int(value["height"]),
            tuple(int(item) for item in value["filled"]),
            tuple(int(item) for item in value["target"]),  # type: ignore[arg-type]
            value.get("ruleset", FOLD_RULESET),
        )

    def coloured_at(self, cell: Cell) -> bool:
        return bool(self.filled[cell[1] * self.width + cell[0]])

    def coloured_cells(self) -> tuple[Cell, ...]:
        return tuple(
            (x, y) for y in range(self.height) for x in range(self.width)
            if self.filled[y * self.width + x]
        )


@dataclass(frozen=True)
class FoldState:
    puzzle_type: str
    step: int
    layers: tuple[Layer, ...]
    extent: Extent

    def to_dict(self) -> dict[str, Any]:
        return {
            "puzzle_type": self.puzzle_type, "step": self.step,
            "extent": list(self.extent), "layers": [list(layer) for layer in self.layers],
        }

    @property
    def size(self) -> tuple[int, int]:
        return self.extent[2] - self.extent[0], self.extent[3] - self.extent[1]

    def value_at(self, layer: Layer, cell: Cell) -> int:
        x0, y0, x1, _ = self.extent
        return layer[(cell[1] - y0) * (x1 - x0) + (cell[0] - x0)]

    def depth_at(self, cell: Cell) -> int:
        return sum(1 for layer in self.layers if self.value_at(layer, cell) != NO_PAPER)

    def coverage_at(self, cell: Cell) -> int:
        """How many layers put a coloured cell on ``cell``.

        The goal wants this to be exactly 1 everywhere inside the target, so a
        bare boolean is no longer enough: 2 is a rules violation, not a hit.
        """
        return sum(1 for layer in self.layers if self.value_at(layer, cell) == COLOURED)

    def covered_at(self, cell: Cell) -> bool:
        """Whether *any* layer colours ``cell`` - what the drawing shows.

        Kept separate from :meth:`coverage_at` on purpose: the renderer paints
        the union of the stack, and at a legal goal state the union and the
        exactly-once coverage agree.
        """
        return any(self.value_at(layer, cell) == COLOURED for layer in self.layers)

    def max_depth(self) -> int:
        x0, y0, x1, y1 = self.extent
        return max(
            (self.depth_at((x, y)) for y in range(y0, y1) for x in range(x0, x1)),
            default=0,
        )


def initial_fold_state(puzzle: FoldPuzzleSpec) -> FoldState:
    layer = tuple(
        COLOURED if puzzle.filled[y * puzzle.width + x] else BLANK
        for y in range(puzzle.height) for x in range(puzzle.width)
    )
    return FoldState("fold", 0, (layer,), (0, 0, puzzle.width, puzzle.height))


def fold_state(state: FoldState, axis: int, line: int, direction: int) -> FoldState:
    """Apply one crease to the full layer stack.

    Every layer is cut by the crease: its stationary half stays where it is and
    keeps its place in the stack, its moving half is reflected across the crease
    and lands *above the whole stationary stack*. Because the flap turns over,
    the moving halves arrive in reversed order - the layer that was at the
    bottom of the flap ends up on top. Getting that reversal wrong leaves the
    outline and the coverage correct while silently corrupting which colour is
    visible, so ``tests/test_fold_logic.py`` asserts the stack order directly.
    """
    if axis not in (0, 1):
        raise ValueError("fold axis must be 0 or 1")
    x0, y0, x1, y1 = state.extent
    lo, hi = (x0, x1) if axis == 0 else (y0, y1)
    a, b = fold_result_extent(lo, hi, line, direction)
    nx0, ny0, nx1, ny1 = (a, y0, b, y1) if axis == 0 else (x0, a, x1, b)
    width, height = x1 - x0, y1 - y0
    new_width, new_height = nx1 - nx0, ny1 - ny0

    stationary: list[Layer | None] = []
    moving: list[Layer | None] = []
    for layer in state.layers:
        stay = [NO_PAPER] * (new_width * new_height)
        flip = [NO_PAPER] * (new_width * new_height)
        any_stay = any_flip = False
        for y in range(y0, y1):
            for x in range(x0, x1):
                value = layer[(y - y0) * width + (x - x0)]
                if value == NO_PAPER:
                    continue
                coordinate = x if axis == 0 else y
                if a <= coordinate < b:
                    stay[(y - ny0) * new_width + (x - nx0)] = value
                    any_stay = True
                else:
                    mirrored = 2 * line - 1 - coordinate
                    mx, my = (mirrored, y) if axis == 0 else (x, mirrored)
                    flip[(my - ny0) * new_width + (mx - nx0)] = value
                    any_flip = True
        stationary.append(tuple(stay) if any_stay else None)
        moving.append(tuple(flip) if any_flip else None)
    layers = tuple(layer for layer in stationary if layer is not None)
    layers += tuple(layer for layer in reversed(moving) if layer is not None)
    return FoldState("fold", state.step + 1, layers, (nx0, ny0, nx1, ny1))


def make_action(state: FoldState, axis: int, line: int, direction: int) -> Action:
    return Action(
        1, "fold_along", f"crease-{axis}-{line}",
        {"axis": [axis], "line": [line], "dir": [direction]},
        {"state_hash": sha256_value(state.to_dict())},
    )


def action_fold(action: Action) -> Fold:
    params = action.params
    for name in ("axis", "line", "dir"):
        value = params.get(name)
        if not isinstance(value, list) or len(value) != 1:
            raise ValueError(f"invalid fold_along parameter: {name}")
    return int(params["axis"][0]), int(params["line"][0]), int(params["dir"][0])


class FoldRules:
    def validate_structure(self, puzzle: FoldPuzzleSpec) -> list[str]:
        errors: list[str] = []
        if puzzle.puzzle_type != "fold":
            errors.append("wrong puzzle type")
        if puzzle.ruleset != FOLD_RULESET:
            errors.append("unsupported ruleset")
        if (puzzle.width, puzzle.height) != (PAPER_WIDTH, PAPER_HEIGHT):
            errors.append("paper shape is not the 6x6 sheet")
        if len(puzzle.filled) != puzzle.width * puzzle.height:
            errors.append("filled vector length differs from the cell count")
            return sorted(set(errors))
        if any(value not in (0, 1) for value in puzzle.filled):
            errors.append("filled value outside {0, 1}")
        target = puzzle.target
        if len(target) != 4:
            errors.append("target is not a 4-tuple")
            return sorted(set(errors))
        tx0, ty0, tx1, ty1 = target
        if not (0 <= tx0 < tx1 <= puzzle.width and 0 <= ty0 < ty1 <= puzzle.height):
            errors.append("target rectangle leaves the sheet")
            return sorted(set(errors))
        if tx1 - tx0 < MIN_TARGET_SIDE or ty1 - ty0 < MIN_TARGET_SIDE:
            errors.append("target rectangle is thinner than the readable minimum")
        if (tx1 - tx0, ty1 - ty0) == (puzzle.width, puzzle.height):
            errors.append("target rectangle is the whole unfolded sheet")
        if sum(puzzle.filled) != (tx1 - tx0) * (ty1 - ty0):
            # Overlap is forbidden and every coloured cell lands inside the
            # target, so any count other than the target's area is unsolvable
            # by arithmetic alone.
            errors.append("coloured cell count differs from the target cell count")
        return sorted(set(errors))

    def initial_state(self, puzzle: FoldPuzzleSpec) -> FoldState:
        return initial_fold_state(puzzle)

    def legal_actions(self, puzzle: FoldPuzzleSpec, state: FoldState) -> tuple[Action, ...]:
        return tuple(make_action(state, *fold) for fold in legal_folds(state.extent))

    def apply(self, puzzle: FoldPuzzleSpec, state: FoldState, action: Action) -> FoldState:
        if action.kind != "fold_along":
            raise ValueError("unsupported action")
        if action.precondition.get("state_hash") != sha256_value(state.to_dict()):
            raise ValueError("action precondition mismatch")
        axis, line, direction = action_fold(action)
        if action.actor_id != f"crease-{axis}-{line}":
            raise ValueError("crease actor/id mismatch")
        return fold_state(state, axis, line, direction)

    def is_goal(self, puzzle: FoldPuzzleSpec, state: FoldState) -> bool:
        if tuple(state.extent) != tuple(puzzle.target):
            return False
        x0, y0, x1, y1 = state.extent
        return all(
            state.coverage_at((x, y)) == 1
            for y in range(y0, y1) for x in range(x0, x1)
        )


@dataclass(frozen=True)
class FoldReplayStep:
    action: Action
    before: FoldState
    after: FoldState


@dataclass(frozen=True)
class FoldReplayTrace:
    initial: FoldState
    steps: tuple[FoldReplayStep, ...]
    final: FoldState


def replay_fold(puzzle: FoldPuzzleSpec, actions: tuple[Action, ...], rules: FoldRules) -> FoldReplayTrace:
    state = rules.initial_state(puzzle)
    initial = state
    steps = []
    for action in actions:
        before = state
        state = rules.apply(puzzle, state, action)
        steps.append(FoldReplayStep(action, before, state))
    return FoldReplayTrace(initial, tuple(steps), state)


# --------------------------------------------------------------------------
# Complete enumeration of fold classes
# --------------------------------------------------------------------------


def _row_masks(puzzle: FoldPuzzleSpec, xmap: dict[int, int]) -> tuple[int, ...]:
    """Per source row, the bitmask of destination columns hit by a colour."""
    rows = [0] * puzzle.height
    for x, y in puzzle.coloured_cells():
        rows[y] |= 1 << xmap[x]
    return tuple(rows)


def _span_mask(lo: int, hi: int) -> int:
    return ((1 << hi) - 1) ^ ((1 << lo) - 1)


def _covers_exactly_once(
    puzzle: FoldPuzzleSpec, xmap: dict[int, int], ymap: dict[int, int], target: Extent
) -> bool:
    """Whether this class lands exactly one coloured cell on every target cell."""
    tx0, ty0, tx1, ty1 = target
    counts: dict[Cell, int] = {}
    for x, y in puzzle.coloured_cells():
        cell = (xmap[x], ymap[y])
        counts[cell] = counts.get(cell, 0) + 1
    return all(
        counts.get((x, y), 0) == 1
        for y in range(ty0, ty1) for x in range(tx0, tx1)
    )


def enumerate_fold_classes(puzzle: FoldPuzzleSpec) -> dict:
    """Every fold class whose sheet ends as ``puzzle.target``, covered exactly once.

    Classes are ``(vertical chain, horizontal chain)`` pairs, which is exactly
    the commutation quotient described in the module docstring.

    The row-mask sweep is kept as the cheap necessary condition ("every target
    cell covered at least once") and the few classes that pass it are then
    checked cell by cell for the exactly-once rule.
    """
    target = tuple(puzzle.target)
    tx0, ty0, tx1, ty1 = target
    x_chains = axis_chains(0, puzzle.width)
    y_chains = axis_chains(0, puzzle.height)
    y_prepared = []
    for chain, extent in y_chains:
        if extent != (ty0, ty1):
            continue
        mapping, _ = chain_coordinate_map(0, puzzle.height, chain)
        y_prepared.append((chain, mapping))
    solutions: list[tuple[tuple[tuple[int, int], ...], tuple[tuple[int, int], ...]]] = []
    shape_only = 0
    expanded = 0
    wanted = _span_mask(tx0, tx1)
    for x_chain, x_extent in x_chains:
        if x_extent != (tx0, tx1):
            continue
        xmap, _ = chain_coordinate_map(0, puzzle.width, x_chain)
        rows = _row_masks(puzzle, xmap)
        for y_chain, ymap in y_prepared:
            if not x_chain and not y_chain:
                continue
            expanded += 1
            shape_only += 1
            accumulated = [0] * puzzle.height
            for y in range(puzzle.height):
                accumulated[ymap[y]] |= rows[y]
            if not all(accumulated[y] == wanted for y in range(ty0, ty1)):
                continue
            if not _covers_exactly_once(puzzle, xmap, ymap, target):
                continue
            solutions.append((x_chain, y_chain))
    if not solutions:
        status = "unsolvable"
    elif len(solutions) > 1:
        status = "ambiguous"
    else:
        status = "unique"
    return {
        "status": status,
        "class_count": len(solutions),
        "shape_reaching_class_count": shape_only,
        "expanded_nodes": expanded,
        "proof": "complete-fold-class-enumeration",
        "_classes": tuple(solutions),
    }


def canonical_folds(x_chain: Iterable[tuple[int, int]], y_chain: Iterable[tuple[int, int]]) -> tuple[Fold, ...]:
    """Canonical representative of a class: every vertical fold, then every horizontal one."""
    return tuple((0, line, direction) for line, direction in x_chain) + tuple(
        (1, line, direction) for line, direction in y_chain
    )


def split_folds(folds: Iterable[Fold]) -> tuple[tuple[tuple[int, int], ...], tuple[tuple[int, int], ...]]:
    folds = tuple(folds)
    return (
        tuple((line, direction) for axis, line, direction in folds if axis == 0),
        tuple((line, direction) for axis, line, direction in folds if axis == 1),
    )


def class_signature(folds: Iterable[Fold]) -> list[list[int]]:
    x_chain, y_chain = split_folds(folds)
    return [list(item) for item in canonical_folds(x_chain, y_chain)]


def signature_hash(folds: Iterable[Fold]) -> str:
    return sha256(canonical_json_bytes(class_signature(folds))).hexdigest()


def actions_for_folds(puzzle: FoldPuzzleSpec, folds: Iterable[Fold], rules: FoldRules) -> tuple[Action, ...]:
    state = rules.initial_state(puzzle)
    actions = []
    for axis, line, direction in folds:
        action = make_action(state, axis, line, direction)
        state = rules.apply(puzzle, state, action)
        actions.append(action)
    return tuple(actions)


class FoldSolver:
    solver_id = "fold-class-enumeration"
    solver_version = "1"

    def __init__(self, rules: FoldRules):
        self.rules = rules

    def analyze(self, puzzle: FoldPuzzleSpec) -> dict:
        return enumerate_fold_classes(puzzle)

    def solve(self, puzzle: FoldPuzzleSpec) -> Solution:
        analysis = self.analyze(puzzle)
        public = {key: value for key, value in analysis.items() if not key.startswith("_")}
        if analysis["status"] == "unsolvable":
            raise FoldSolveRejected("NO_FOLD_SEQUENCE", public)
        if analysis["status"] != "unique":
            raise FoldSolveRejected("MULTIPLE_FOLD_SEQUENCES", public)
        x_chain, y_chain = analysis["_classes"][0]
        folds = canonical_folds(x_chain, y_chain)
        state = self.rules.initial_state(puzzle)
        initial_hash = sha256_value(state.to_dict())
        actions = actions_for_folds(puzzle, folds, self.rules)
        trace = replay_fold(puzzle, actions, self.rules)
        if not self.rules.is_goal(puzzle, trace.final):
            raise FoldSolveRejected("SOLVER_INTERNAL_ERROR", public)
        return Solution(
            "1.0.0", self.solver_id, self.solver_version, "proven_unique_fold_class",
            actions, initial_hash, sha256_value(trace.final.to_dict()),
            len(actions), analysis["expanded_nodes"], "unique:" + signature_hash(folds),
        )


def solution_folds(solution: Solution) -> tuple[Fold, ...]:
    return tuple(action_fold(action) for action in solution.actions)


def validate_fold_solution(puzzle: FoldPuzzleSpec, solution: Solution, rules: FoldRules) -> list[str]:
    failures = rules.validate_structure(puzzle)
    if failures:
        return failures
    try:
        trace = replay_fold(puzzle, solution.actions, rules)
    except ValueError as error:
        failures.append(f"illegal action: {error}")
        return failures
    if not rules.is_goal(puzzle, trace.final):
        failures.append("folded sheet does not fill the target rectangle")
    if sha256_value(trace.initial.to_dict()) != solution.initial_state_hash:
        failures.append("initial state hash mismatch")
    if sha256_value(trace.final.to_dict()) != solution.final_state_hash:
        failures.append("final state hash mismatch")
    if solution.cost != len(solution.actions):
        failures.append("solution cost mismatch")
    folds = solution_folds(solution)
    if any(action.kind != "fold_along" for action in solution.actions):
        failures.append("solution carries a non-fold action")
    analysis = FoldSolver(rules).analyze(puzzle)
    if analysis["status"] != "unique":
        failures.append("fold class is not provably unique")
    elif solution.answer_equivalence_key != "unique:" + signature_hash(folds):
        failures.append("answer equivalence key mismatch")
    elif class_signature(folds) != class_signature(canonical_folds(*analysis["_classes"][0])):
        failures.append("solution differs from the unique fold class oracle")
    return failures


# --------------------------------------------------------------------------
# Generation (forward: draw a colouring, enumerate, keep a unique target)
# --------------------------------------------------------------------------


def candidate_targets(puzzle_without_target: FoldPuzzleSpec, preset: dict) -> list[tuple[Extent, tuple[Fold, ...]]]:
    """Targets this colouring reaches, exactly once per cell, by one fold class.

    Only used to re-check a colouring after the fact; generation itself runs
    the other way round (see :func:`draw_candidate`).
    """
    minimum = int(preset.get("min_target_side", MIN_TARGET_SIDE))
    min_folds = int(preset.get("min_folds", MIN_FOLD_COUNT))
    max_folds = int(preset.get("max_folds", 4))
    coloured = sum(puzzle_without_target.filled)
    found: list[tuple[Extent, tuple[Fold, ...]]] = []
    for x_chain, x_extent in axis_chains(0, puzzle_without_target.width):
        if x_extent[1] - x_extent[0] < minimum or not x_chain:
            continue
        for y_chain, y_extent in axis_chains(0, puzzle_without_target.height):
            if y_extent[1] - y_extent[0] < minimum or not y_chain:
                continue
            total = len(x_chain) + len(y_chain)
            if not min_folds <= total <= max_folds:
                continue
            target: Extent = (x_extent[0], y_extent[0], x_extent[1], y_extent[1])
            # Overlap is forbidden, so a target whose area differs from the
            # colour count cannot be reached at all - skip it before enumerating.
            if (target[2] - target[0]) * (target[3] - target[1]) != coloured:
                continue
            probe = FoldPuzzleSpec(
                puzzle_without_target.schema_version, puzzle_without_target.puzzle_type,
                puzzle_without_target.generator_version, puzzle_without_target.width,
                puzzle_without_target.height, puzzle_without_target.filled, target,
                puzzle_without_target.ruleset,
            )
            analysis = enumerate_fold_classes(probe)
            if analysis["status"] != "unique":
                continue
            folds = canonical_folds(*analysis["_classes"][0])
            if len(folds) != total:
                continue
            found.append((target, folds))
    return found


def fold_plans(preset: dict, width: int, height: int) -> list[tuple[
    tuple[tuple[int, int], ...], tuple[int, int], tuple[tuple[int, int], ...], tuple[int, int]
]]:
    """Every ``(vertical chain, horizontal chain)`` the preset's band allows."""
    minimum = int(preset.get("min_target_side", MIN_TARGET_SIDE))
    min_folds = int(preset.get("min_folds", MIN_FOLD_COUNT))
    max_folds = int(preset.get("max_folds", 4))
    plans = []
    for x_chain, x_extent in axis_chains(0, width):
        if not x_chain or x_extent[1] - x_extent[0] < minimum:
            continue
        for y_chain, y_extent in axis_chains(0, height):
            if not y_chain or y_extent[1] - y_extent[0] < minimum:
                continue
            if not min_folds <= len(x_chain) + len(y_chain) <= max_folds:
                continue
            plans.append((x_chain, x_extent, y_chain, y_extent))
    plans.sort()
    return plans


def draw_candidate(rng: StableRng, preset: dict) -> FoldPuzzleSpec:
    """One colouring that folds onto its target with no cell covered twice.

    Generation runs backwards from the answer now, and it has to. Under the
    old overlap-tolerant rule a random colouring almost always had *some*
    target it covered, so drawing first and searching for a target afterwards
    worked. Exactly-once coverage is an exact-cover condition: measured over
    200 random colourings whose colour count was pinned to a reachable target
    area, 0.0% (easy) to 3.5% (target band) admitted any exact target at all.
    So the generator picks the fold class first and then paints backwards -
    for each target cell it colours exactly one of the source cells that fold
    onto it. That is exact by construction, and the only thing left to filter
    is whether the class that produced it is the *only* one.

    The colour count is not a preset knob any more: it is the target's area.
    """
    width = int(preset.get("width", PAPER_WIDTH))
    height = int(preset.get("height", PAPER_HEIGHT))
    plans = fold_plans(preset, width, height)
    if not plans:
        raise ValueError("FOLD_NO_FOLD_PLAN: no fold class matches the band's fold and target bounds")
    x_chain, x_extent, y_chain, y_extent = plans[rng.randbelow(len(plans))]
    xmap, _ = chain_coordinate_map(0, width, x_chain)
    ymap, _ = chain_coordinate_map(0, height, y_chain)
    x_preimage: dict[int, list[int]] = {}
    y_preimage: dict[int, list[int]] = {}
    for source, landing in sorted(xmap.items()):
        x_preimage.setdefault(landing, []).append(source)
    for source, landing in sorted(ymap.items()):
        y_preimage.setdefault(landing, []).append(source)
    filled = [0] * (width * height)
    for landing_y in range(y_extent[0], y_extent[1]):
        for landing_x in range(x_extent[0], x_extent[1]):
            columns = x_preimage[landing_x]
            rows = y_preimage[landing_y]
            x = columns[rng.randbelow(len(columns))]
            y = rows[rng.randbelow(len(rows))]
            filled[y * width + x] = 1
    target: Extent = (x_extent[0], y_extent[0], x_extent[1], y_extent[1])
    return FoldPuzzleSpec(
        "1.0.0", "fold", "fold-gen-1", width, height, tuple(filled), target, FOLD_RULESET,
    )


def generate_fold(rng: StableRng, preset: dict) -> FoldPuzzleSpec:
    """Backwards generation (fold class first) with an in-generator quality screen.

    Nothing produced here is trusted: the pipeline re-solves and re-filters
    every candidate.
    """
    rules = FoldRules()
    solver = FoldSolver(rules)
    attempts = int(preset.get("search_attempts", 24))
    fallback: FoldPuzzleSpec | None = None
    for _ in range(max(1, attempts)):
        try:
            candidate = draw_candidate(rng, preset)
        except ValueError:
            continue
        if rules.validate_structure(candidate):
            continue
        if fallback is None:
            fallback = candidate
        try:
            solution = solver.solve(candidate)
        except FoldSolveRejected:
            continue
        report = fold_difficulty_report(candidate, solution, rules)
        if fold_quality_rejection(report, preset.get("band", "medium")) is None:
            return candidate
    if fallback is None:
        raise ValueError("FOLD_LAYOUT_FAILED: no structurally valid sheet was produced")
    return fallback


# --------------------------------------------------------------------------
# Difficulty
# --------------------------------------------------------------------------


def decoy_crease_count(puzzle: FoldPuzzleSpec) -> int:
    """Fold classes that reach the target *outline* but fail to fill it.

    This is the trap the solver has to see through: the shape is right and the
    colour is not. Counting merely-legal creases would be a constant.
    """
    analysis = enumerate_fold_classes(puzzle)
    return max(0, analysis["shape_reaching_class_count"] - analysis["class_count"])


def greedy_fold_succeeds(puzzle: FoldPuzzleSpec, rules: FoldRules) -> bool:
    """Whether folding for maximum immediate coverage happens to solve it."""
    state = rules.initial_state(puzzle)
    tx0, ty0, tx1, ty1 = puzzle.target
    for _ in range(puzzle.width + puzzle.height):
        if rules.is_goal(puzzle, state):
            return True
        best = None
        for axis, line, direction in legal_folds(state.extent):
            nxt = fold_state(state, axis, line, direction)
            nx0, ny0, nx1, ny1 = nxt.extent
            if not (tx0 <= nx0 and nx1 <= tx1 and ty0 <= ny0 and ny1 <= ty1):
                # A greedy solver still only considers folds that keep the
                # sheet inside the target; otherwise it wanders off instantly.
                if not (nx0 <= tx0 and tx1 <= nx1 and ny0 <= ty0 and ty1 <= ny1):
                    continue
            cells = [(x, y) for y in range(ny0, ny1) for x in range(nx0, nx1)]
            coverage = [nxt.coverage_at(cell) for cell in cells]
            exact = sum(1 for value in coverage if value == 1)
            doubled = sum(value - 1 for value in coverage if value > 1)
            # Overlap is now a rules violation, so a greedy folder that walked
            # into one would not be greedy: it maximises exactly-covered cells
            # and breaks ties against stacking colour on colour.
            score = (exact / max(1, len(cells)), -doubled, exact, -(axis), -line, -direction)
            if best is None or score > best[0]:
                best = (score, nxt)
        if best is None:
            return False
        state = best[1]
    return rules.is_goal(puzzle, state)


def fold_difficulty_preset(band: str) -> dict:
    from .preset_loader import difficulty_preset as load_difficulty_preset
    return load_difficulty_preset("fold", band)


def fold_difficulty_report(puzzle: FoldPuzzleSpec, solution: Solution, rules: FoldRules) -> dict:
    analysis = FoldSolver(rules).analyze(puzzle)
    folds = solution_folds(solution)
    trace = replay_fold(puzzle, solution.actions, rules)
    x_chain, y_chain = split_folds(folds)
    tx0, ty0, tx1, ty1 = puzzle.target
    target_cells = (tx1 - tx0) * (ty1 - ty0)
    filled_count = sum(puzzle.filled)
    decoys = max(0, analysis["shape_reaching_class_count"] - analysis["class_count"])
    greedy = greedy_fold_succeeds(puzzle, rules)
    depth = trace.final.max_depth()
    mechanical = {
        "fold_count": len(folds),
        "vertical_folds": len(x_chain),
        "horizontal_folds": len(y_chain),
        "decoy_crease_count": decoys,
        "greedy_success": greedy,
        "final_layer_depth": depth,
        "filled_cells": filled_count,
        # ``filled_ratio`` is gone: with overlap forbidden the colour count is
        # pinned to the target's area, so it was an exact restatement of
        # ``target_ratio`` under a name that suggested an independent signal.
        "target_ratio": round(target_cells / (puzzle.width * puzzle.height), 4),
        "target_cells": target_cells,
        "sheet_cells": puzzle.width * puzzle.height,
        "solver_expanded_nodes": solution.expanded_nodes,
        "shape_reaching_classes": analysis["shape_reaching_class_count"],
    }
    # fold_count alone separates the bands by 60 points, less than a band's own
    # spread, so the deceptiveness terms carry the rest: how many shape-correct
    # traps exist, how deep the final stack is, whether a greedy folder is led
    # straight to the answer, and how far the sheet has to shrink - the last
    # term used to read the colour count, which is now the target's area.
    mechanical["difficulty_score"] = (
        60 * mechanical["fold_count"]
        + 3 * decoys
        + 12 * depth
        + (0 if greedy else 45)
        + 2 * (puzzle.width * puzzle.height - target_cells)
    )
    return {
        "mechanical": mechanical,
        "human": {
            "status": "uncalibrated-fold-v1", "model_version": None,
            "calibration_scope": None, "predicted_correct_time_ms": None,
            "p_solve_before_reveal": None,
            "features": {
                "fold_count": mechanical["fold_count"],
                "decoy_crease_count": decoys,
                "greedy_success": greedy,
                "final_layer_depth": depth,
                "target_ratio": mechanical["target_ratio"],
            },
        },
        "solution_uniqueness": {
            "status": analysis["status"],
            "fold_class_count": analysis["class_count"],
            "shape_reaching_class_count": analysis["shape_reaching_class_count"],
            "proof": analysis["proof"],
            "expanded_nodes": analysis["expanded_nodes"],
            "equivalence_policy": FOLD_EQUIVALENCE_POLICY,
            "equivalence_policy_version": FOLD_EQUIVALENCE_VERSION,
            "normalized_signature": class_signature(folds),
            "normalized_signature_hash": signature_hash(folds),
        },
        "requested_band": None, "accepted_band": None, "quality_preset": None,
    }


def fold_quality_rejection(difficulty: dict, requested_band: str = "medium") -> str | None:
    preset = fold_difficulty_preset(requested_band)
    metrics = difficulty["mechanical"]
    difficulty["requested_band"] = requested_band
    difficulty["quality_preset"] = preset["name"]
    uniqueness = difficulty.get("solution_uniqueness")
    if uniqueness is not None and uniqueness["status"] != "unique":
        return "MULTIPLE_FOLD_SEQUENCES" if uniqueness["status"] == "ambiguous" else "NO_FOLD_SEQUENCE"
    # Fewer than two folds would leave the neutrality perturbation - which
    # reorders the vertical and horizontal fold groups - with nothing to swap.
    if metrics["fold_count"] < max(MIN_FOLD_COUNT, preset["min_folds"]):
        return "FOLD_COUNT_TOO_LOW"
    if metrics["fold_count"] > preset["max_folds"]:
        return "ANIMATION_TOO_LONG"
    # Both axes must be used, or the two interleavings coincide and the
    # perturbation cannot change the reveal frame.
    if metrics["vertical_folds"] < 1 or metrics["horizontal_folds"] < 1:
        return "SINGLE_AXIS_SOLUTION"
    if metrics["decoy_crease_count"] > preset["max_decoy_creases"]:
        return "OUTSIDE_BAND"
    if metrics["difficulty_score"] < preset["min_difficulty_score"]:
        return "TOO_TRIVIAL"
    if metrics["difficulty_score"] > preset["max_difficulty_score"]:
        return "OUTSIDE_BAND"
    difficulty["accepted_band"] = requested_band
    difficulty["human"]["status"] = "uncalibrated-fold-v1"
    return None
