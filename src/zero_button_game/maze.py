from __future__ import annotations

from collections import deque
from math import ceil

from .core import StableRng, sha256_value
from .models import Action, PuzzleSpec, PuzzleState, ReplayStep, ReplayTrace, Solution

Cell = tuple[int, int]
Edge = tuple[Cell, Cell]
NEIGHBORS: tuple[Cell, ...] = ((0, -1), (1, 0), (0, 1), (-1, 0))


def normalized_edge(a: Cell, b: Cell) -> Edge:
    return (a, b) if a < b else (b, a)


def generate_maze(
    rng: StableRng, width: int = 7, height: int = 7,
    endpoint_profile: str = "corners",
) -> PuzzleSpec:
    if not (5 <= width <= 9 and 5 <= height <= 9):
        raise ValueError("maze dimensions must be between 5 and 9")
    start: Cell = (0, 0)
    stack = [start]
    visited = {start}
    edges: list[Edge] = []
    while stack:
        x, y = stack[-1]
        choices = [(x + dx, y + dy) for dx, dy in NEIGHBORS]
        choices = [cell for cell in choices if 0 <= cell[0] < width and 0 <= cell[1] < height and cell not in visited]
        rng.shuffle(choices)
        if not choices:
            stack.pop()
            continue
        nxt = choices[0]
        edges.append(normalized_edge(stack[-1], nxt))
        visited.add(nxt)
        stack.append(nxt)
    sorted_edges = tuple(sorted(edges))
    recipe = None
    if endpoint_profile == "corners":
        goal = (width - 1, height - 1)
    elif endpoint_profile in {"detour", "folded", "deceptive"}:
        start, goal = _select_expert_endpoints(width, height, sorted_edges, endpoint_profile)
    elif endpoint_profile == "mixed":
        recipe = mixed_trait_recipe(rng)
        start, goal = _select_expert_endpoints(width, height, sorted_edges, endpoint_profile, recipe)
    else:
        raise ValueError(f"unknown endpoint profile: {endpoint_profile}")
    if endpoint_profile == "corners":
        ruleset = "perfect-maze-v1"
        generator_version = "maze-gen-1"
    elif endpoint_profile == "mixed":
        encoded = "+".join(f"{trait}@{recipe['weights'][trait]}" for trait in recipe["active_traits"])
        ruleset = f"perfect-maze-v1:mixed-v1:{encoded}"
        generator_version = "maze-gen-3"
    else:
        ruleset = f"perfect-maze-v1:{endpoint_profile}"
        generator_version = "maze-gen-2"
    return PuzzleSpec("1.0.0", "maze", generator_version, width, height, start, goal, sorted_edges, ruleset)


class MazeRules:
    def validate_structure(self, puzzle: PuzzleSpec) -> list[str]:
        errors: list[str] = []
        if puzzle.puzzle_type != "maze":
            errors.append("wrong puzzle type")
        if not (5 <= puzzle.width <= 9 and 5 <= puzzle.height <= 9):
            errors.append("dimensions outside 5..9")
        cells = {(x, y) for y in range(puzzle.height) for x in range(puzzle.width)}
        if puzzle.start not in cells or puzzle.goal not in cells or puzzle.start == puzzle.goal:
            errors.append("invalid start or goal")
        edge_set = set(puzzle.edges)
        if len(edge_set) != len(puzzle.edges):
            errors.append("duplicate edge")
        for a, b in puzzle.edges:
            if a not in cells or b not in cells or abs(a[0] - b[0]) + abs(a[1] - b[1]) != 1:
                errors.append("invalid edge")
        if len(puzzle.edges) != len(cells) - 1:
            errors.append("perfect maze must have cells-1 edges")
        reached = {puzzle.start}
        queue = deque([puzzle.start])
        while queue:
            for nxt in self.neighbors(puzzle, queue.popleft()):
                if nxt not in reached:
                    reached.add(nxt)
                    queue.append(nxt)
        if reached != cells:
            errors.append("maze is disconnected")
        return errors

    def initial_state(self, puzzle: PuzzleSpec) -> PuzzleState:
        return PuzzleState("maze", 0, puzzle.start, (puzzle.start,))

    def neighbors(self, puzzle: PuzzleSpec, cell: Cell) -> list[Cell]:
        edges = set(puzzle.edges)
        result = []
        for dx, dy in NEIGHBORS:
            nxt = (cell[0] + dx, cell[1] + dy)
            if normalized_edge(cell, nxt) in edges:
                result.append(nxt)
        return result

    def legal_actions(self, puzzle: PuzzleSpec, state: PuzzleState) -> tuple[Action, ...]:
        before_hash = sha256_value(state.to_dict())
        return tuple(Action(1, "traverse_edge", "traveler", {"from_node": list(state.current), "to_node": list(nxt)}, {"state_hash": before_hash}) for nxt in self.neighbors(puzzle, state.current))

    def apply(self, puzzle: PuzzleSpec, state: PuzzleState, action: Action) -> PuzzleState:
        if action.kind != "traverse_edge" or action.actor_id != "traveler":
            raise ValueError("unsupported action")
        if action.precondition.get("state_hash") != sha256_value(state.to_dict()):
            raise ValueError("action precondition mismatch")
        src = tuple(action.params.get("from_node", ()))
        dst = tuple(action.params.get("to_node", ()))
        if src != state.current or dst not in self.neighbors(puzzle, state.current):
            raise ValueError("illegal maze traversal")
        return PuzzleState("maze", state.step + 1, dst, state.visited + (dst,))

    def is_goal(self, puzzle: PuzzleSpec, state: PuzzleState) -> bool:
        return state.current == puzzle.goal


class MazeSolver:
    solver_id = "maze-bfs"
    solver_version = "1"

    def __init__(self, rules: MazeRules):
        self.rules = rules

    def solve(self, puzzle: PuzzleSpec, node_budget: int = 10_000) -> Solution:
        queue = deque([puzzle.start])
        previous: dict[Cell, Cell | None] = {puzzle.start: None}
        expanded = 0
        while queue:
            cell = queue.popleft()
            expanded += 1
            if expanded > node_budget:
                raise RuntimeError("SOLVE_BUDGET_EXCEEDED")
            if cell == puzzle.goal:
                break
            for nxt in self.rules.neighbors(puzzle, cell):
                if nxt not in previous:
                    previous[nxt] = cell
                    queue.append(nxt)
        if puzzle.goal not in previous:
            raise RuntimeError("UNSOLVABLE")
        path: list[Cell] = []
        current: Cell | None = puzzle.goal
        while current is not None:
            path.append(current)
            current = previous[current]
        path.reverse()
        state = self.rules.initial_state(puzzle)
        initial_hash = sha256_value(state.to_dict())
        actions: list[Action] = []
        for src, dst in zip(path, path[1:]):
            action = Action(1, "traverse_edge", "traveler", {"from_node": list(src), "to_node": list(dst)}, {"state_hash": sha256_value(state.to_dict())})
            state = self.rules.apply(puzzle, state, action)
            actions.append(action)
        answer_key = "path:" + ">".join(f"{x},{y}" for x, y in path)
        return Solution("1.0.0", self.solver_id, self.solver_version, "proven_shortest", tuple(actions), initial_hash, sha256_value(state.to_dict()), len(actions), expanded, answer_key)


def replay(puzzle: PuzzleSpec, actions: tuple[Action, ...], rules: MazeRules) -> ReplayTrace:
    state = rules.initial_state(puzzle)
    initial = state
    steps: list[ReplayStep] = []
    for action in actions:
        before = state
        state = rules.apply(puzzle, state, action)
        steps.append(ReplayStep(action, before, state))
    return ReplayTrace(initial, tuple(steps), state)


def validate_maze_solution(puzzle: PuzzleSpec, solution: Solution, rules: MazeRules) -> list[str]:
    failures = rules.validate_structure(puzzle)
    try:
        trace = replay(puzzle, solution.actions, rules)
    except ValueError as error:
        failures.append(f"illegal action: {error}")
        return failures
    if not rules.is_goal(puzzle, trace.final):
        failures.append("final state is not goal")
    if sha256_value(trace.initial.to_dict()) != solution.initial_state_hash:
        failures.append("initial state hash mismatch")
    if sha256_value(trace.final.to_dict()) != solution.final_state_hash:
        failures.append("final state hash mismatch")
    if solution.cost != len(solution.actions):
        failures.append("solution cost mismatch")
    return failures


def _cell_path(puzzle: PuzzleSpec, rules: MazeRules) -> list[Cell]:
    queue = deque([puzzle.start])
    previous: dict[Cell, Cell | None] = {puzzle.start: None}
    while queue:
        cell = queue.popleft()
        if cell == puzzle.goal:
            break
        for nxt in rules.neighbors(puzzle, cell):
            if nxt not in previous:
                previous[nxt] = cell
                queue.append(nxt)
    if puzzle.goal not in previous:
        raise ValueError("maze endpoints are disconnected")
    path: list[Cell] = []
    current: Cell | None = puzzle.goal
    while current is not None:
        path.append(current)
        current = previous[current]
    return list(reversed(path))


def _path_metrics(puzzle: PuzzleSpec, path: list[Cell], rules: MazeRules) -> dict:
    path_set = set(path)
    solution_cost = len(path) - 1
    goal = puzzle.goal
    branch_depths: list[int] = []
    branch_sizes: list[int] = []
    decision_count = 0
    goal_zone_false_leads = 0
    competitive_false_leads = 0
    visually_similar_false_leads = 0
    goalward_false_leads = 0
    misleading_goalward_false_leads = 0
    correct_wrong_way_decisions = 0
    goal_zone_start = ceil(solution_cost * 0.65)
    decision_indices: list[int] = []
    off_path_by_index: dict[int, list[Cell]] = {}
    for path_index, cell in enumerate(path[:-1]):
        off_path = [nxt for nxt in rules.neighbors(puzzle, cell) if nxt not in path_set]
        if off_path:
            decision_count += 1
            decision_indices.append(path_index)
            off_path_by_index[path_index] = off_path
    for path_index, off_path in off_path_by_index.items():
        cell = path[path_index]
        correct_next = path[path_index + 1]
        cell_goal_distance = abs(cell[0] - goal[0]) + abs(cell[1] - goal[1])
        correct_goalward = abs(correct_next[0] - goal[0]) + abs(correct_next[1] - goal[1]) < cell_goal_distance
        if not correct_goalward:
            correct_wrong_way_decisions += 1
        correct_arm_length = 1
        cursor = path_index + 1
        while cursor < len(path) - 1 and len(rules.neighbors(puzzle, path[cursor])) == 2:
            correct_arm_length += 1
            cursor += 1
        next_decision = next((index for index in decision_indices if index > path_index), len(path) - 1)
        correct_commitment = max(1, next_decision - path_index)
        for branch_start in off_path:
            queue = deque([(branch_start, 1)])
            seen = {cell, branch_start}
            branch_depth = 1
            branch_size = 0
            while queue:
                branch_cell, depth = queue.popleft()
                branch_depth = max(branch_depth, depth)
                branch_size += 1
                for nxt in rules.neighbors(puzzle, branch_cell):
                    if nxt not in seen and nxt not in path_set:
                        seen.add(nxt)
                        queue.append((nxt, depth + 1))
            branch_depths.append(branch_depth)
            branch_sizes.append(branch_size)
            if branch_depth >= max(3, correct_commitment):
                competitive_false_leads += 1
            previous = cell
            branch_cursor = branch_start
            false_arm_length = 1
            while len(rules.neighbors(puzzle, branch_cursor)) == 2:
                continuations = [
                    nxt for nxt in rules.neighbors(puzzle, branch_cursor)
                    if nxt != previous and nxt not in path_set
                ]
                if len(continuations) != 1:
                    break
                previous, branch_cursor = branch_cursor, continuations[0]
                false_arm_length += 1
            if min(false_arm_length, correct_arm_length) >= 2 and abs(false_arm_length - correct_arm_length) <= 1:
                visually_similar_false_leads += 1
            branch_goalward = abs(branch_start[0] - goal[0]) + abs(branch_start[1] - goal[1]) < cell_goal_distance
            if branch_goalward:
                goalward_false_leads += 1
                if not correct_goalward:
                    misleading_goalward_false_leads += 1
            if path_index >= goal_zone_start:
                goal_zone_false_leads += 1
    false_leads = len(branch_depths)
    deep_false_leads = sum(depth >= 3 for depth in branch_depths)
    directions = [(b[0] - a[0], b[1] - a[1]) for a, b in zip(path, path[1:])]
    turn_count = sum(first != second for first, second in zip(directions, directions[1:]))
    straight_runs: list[int] = []
    if directions:
        run_length = 1
        for first, second in zip(directions, directions[1:]):
            if first == second:
                run_length += 1
            else:
                straight_runs.append(run_length)
                run_length = 1
        straight_runs.append(run_length)
    wrong_way_steps = 0
    for before, after in zip(path, path[1:]):
        before_distance = abs(before[0] - goal[0]) + abs(before[1] - goal[1])
        after_distance = abs(after[0] - goal[0]) + abs(after[1] - goal[1])
        wrong_way_steps += after_distance > before_distance
    edge_set = set(puzzle.edges)
    wall_contacts: list[tuple[int, int]] = []
    for first_index, first in enumerate(path):
        for second_index in range(first_index + 2, len(path)):
            second = path[second_index]
            if abs(first[0] - second[0]) + abs(first[1] - second[1]) == 1 and normalized_edge(first, second) not in edge_set:
                wall_contacts.append((first_index, second_index))
    direct_manhattan = abs(puzzle.start[0] - goal[0]) + abs(puzzle.start[1] - goal[1])
    path_contact_cells = len({index for pair in wall_contacts for index in pair})
    return {
        "solution_cost": solution_cost,
        "direct_manhattan": direct_manhattan,
        "path_stretch_ratio": round(solution_cost / max(1, direct_manhattan), 4),
        "wrong_way_steps": wrong_way_steps,
        "turn_count": turn_count,
        "turn_rate": round(turn_count / max(1, solution_cost - 1), 4),
        "longest_straight_run": max(straight_runs, default=0),
        "path_wall_contacts": len(wall_contacts),
        "path_contact_cells": path_contact_cells,
        "decision_count": decision_count,
        "false_leads": false_leads,
        "deep_false_leads": deep_false_leads,
        "competitive_false_leads": competitive_false_leads,
        "visually_similar_false_leads": visually_similar_false_leads,
        "goalward_false_leads": goalward_false_leads,
        "misleading_goalward_false_leads": misleading_goalward_false_leads,
        "correct_wrong_way_decisions": correct_wrong_way_decisions,
        "goal_zone_false_leads": goal_zone_false_leads,
        "false_lead_depth_max": max(branch_depths, default=0),
        "false_lead_depth_total": sum(branch_depths),
        "false_lead_cells": sum(branch_sizes),
        "edge_count": len(puzzle.edges),
    }


MIXED_TRAIT_TARGETS = {
    "geometric_detour": {"path_stretch_ratio": 3.2, "wrong_way_steps": 13},
    "folded_path": {"path_wall_contacts": 12, "path_contact_cells": 18},
    "competitive_branches": {"competitive_false_leads": 3, "visually_similar_false_leads": 2},
    "goal_zone_traps": {"goal_zone_false_leads": 2, "misleading_goalward_false_leads": 2},
}


def mixed_trait_recipe(rng: StableRng) -> dict:
    traits = list(MIXED_TRAIT_TARGETS)
    rng.shuffle(traits)
    active_count = 2 + rng.randbelow(2)
    active = tuple(sorted(traits[:active_count]))
    weights = {trait: 1 + rng.randbelow(3) for trait in active}
    return {"version": "mixed-traits-v1", "active_traits": active, "weights": weights}


def _mixed_recipe_from_ruleset(ruleset: str) -> dict | None:
    prefix = "perfect-maze-v1:mixed-v1:"
    if not ruleset.startswith(prefix):
        return None
    weights = {}
    for item in ruleset[len(prefix):].split("+"):
        trait, weight = item.rsplit("@", 1)
        if trait not in MIXED_TRAIT_TARGETS:
            raise ValueError(f"unknown mixed maze trait: {trait}")
        weights[trait] = int(weight)
    active = tuple(sorted(weights))
    if len(active) < 2:
        raise ValueError("mixed maze recipe must activate at least two traits")
    return {"version": "mixed-traits-v1", "active_traits": active, "weights": weights}


def _trait_satisfied(metrics: dict, trait: str) -> bool:
    return all(metrics[metric] >= minimum for metric, minimum in MIXED_TRAIT_TARGETS[trait].items())


def _mixed_trait_score(metrics: dict, recipe: dict) -> tuple:
    trait_scores = []
    for trait in recipe["active_traits"]:
        normalized = sum(metrics[metric] / minimum for metric, minimum in MIXED_TRAIT_TARGETS[trait].items())
        trait_scores.append(recipe["weights"][trait] * normalized)
    satisfied = sum(_trait_satisfied(metrics, trait) for trait in recipe["active_traits"])
    return (
        satisfied,
        round(sum(trait_scores), 6),
        metrics["false_lead_depth_total"], metrics["turn_count"],
    )


def _select_expert_endpoints(
    width: int, height: int, edges: tuple[Edge, ...], profile: str,
    recipe: dict | None = None,
) -> tuple[Cell, Cell]:
    rules = MazeRules()
    cells = [(x, y) for y in range(height) for x in range(width)]
    candidates: list[tuple[tuple, Cell, Cell]] = []
    fallbacks: list[tuple[tuple, Cell, Cell]] = []
    for start in cells:
        for goal in cells:
            if start == goal:
                continue
            direct = abs(start[0] - goal[0]) + abs(start[1] - goal[1])
            if direct < 4:
                continue
            puzzle = PuzzleSpec("1.0.0", "maze", "maze-gen-2", width, height, start, goal, edges, f"perfect-maze-v1:{profile}")
            path = _cell_path(puzzle, rules)
            if not 36 <= len(path) - 1 <= 40:
                continue
            metrics = _path_metrics(puzzle, path, rules)
            if profile == "detour":
                score = (
                    metrics["path_stretch_ratio"], metrics["wrong_way_steps"],
                    metrics["correct_wrong_way_decisions"], metrics["turn_count"],
                    metrics["path_wall_contacts"], start, goal,
                )
            elif profile == "folded":
                score = (
                    metrics["path_wall_contacts"], metrics["path_contact_cells"],
                    metrics["turn_count"], metrics["path_stretch_ratio"],
                    metrics["wrong_way_steps"], start, goal,
                )
            elif profile == "deceptive":
                score = (
                    metrics["competitive_false_leads"] * 3
                    + metrics["visually_similar_false_leads"] * 2
                    + metrics["misleading_goalward_false_leads"],
                    metrics["competitive_false_leads"], metrics["visually_similar_false_leads"],
                    metrics["goalward_false_leads"], metrics["deep_false_leads"],
                    metrics["decision_count"], metrics["path_stretch_ratio"], start, goal,
                )
            else:
                if recipe is None:
                    raise ValueError("mixed endpoint profile requires a trait recipe")
                score = _mixed_trait_score(metrics, recipe) + (start, goal)
            entry = (score, start, goal)
            fallbacks.append(entry)
            core_pass = metrics["decision_count"] >= 6 and metrics["false_leads"] >= 6 and metrics["deep_false_leads"] >= 4
            recipe_pass = profile != "mixed" or all(_trait_satisfied(metrics, trait) for trait in recipe["active_traits"])
            if core_pass and recipe_pass:
                candidates.append(entry)
    if not fallbacks:
        raise ValueError(f"no readable expert endpoints found for {profile}")
    _, start, goal = max(candidates or fallbacks)
    return start, goal


def difficulty_report(puzzle: PuzzleSpec, solution: Solution, rules: MazeRules) -> dict:
    path = [puzzle.start] + [tuple(action.params["to_node"]) for action in solution.actions]
    mechanical = _path_metrics(puzzle, path, rules)
    mechanical["expanded_nodes"] = solution.expanded_nodes
    edge_density = len(puzzle.edges) / (puzzle.width * (puzzle.height - 1) + puzzle.height * (puzzle.width - 1))
    report = {
        "mechanical": mechanical,
        "human": {
            "status": "uncalibrated", "model_version": None, "predicted_correct_time_ms": None,
            "p_solve_before_reveal": None,
            "features": {
                "recognition_load": 3, "visual_clutter": round(edge_density, 4),
                "mental_steps": solution.cost, "decision_count": mechanical["decision_count"],
                "false_leads": mechanical["false_leads"],
                "deep_false_leads": mechanical["deep_false_leads"],
                "goal_zone_false_leads": mechanical["goal_zone_false_leads"],
                "path_stretch_ratio": mechanical["path_stretch_ratio"],
                "wrong_way_steps": mechanical["wrong_way_steps"],
                "turn_count": mechanical["turn_count"],
                "path_wall_contacts": mechanical["path_wall_contacts"],
                "competitive_false_leads": mechanical["competitive_false_leads"],
                "visually_similar_false_leads": mechanical["visually_similar_false_leads"],
            },
        },
        "requested_band": None, "accepted_band": None, "quality_preset": None,
    }
    recipe = _mixed_recipe_from_ruleset(puzzle.ruleset)
    if recipe is not None:
        traits = {}
        for trait, targets in MIXED_TRAIT_TARGETS.items():
            observed = {metric: mechanical[metric] for metric in targets}
            traits[trait] = {
                "active": trait in recipe["active_traits"],
                "weight": recipe["weights"].get(trait, 0),
                "targets": dict(targets),
                "observed": observed,
                "satisfied": _trait_satisfied(mechanical, trait),
            }
        report["generation_traits"] = {
            "recipe_version": recipe["version"],
            "active_traits": list(recipe["active_traits"]),
            "minimum_active_traits": 2,
            "weights": dict(recipe["weights"]),
            "traits": traits,
            "active_satisfied_count": sum(traits[trait]["satisfied"] for trait in recipe["active_traits"]),
            "observed_satisfied_traits": [trait for trait in MIXED_TRAIT_TARGETS if traits[trait]["satisfied"]],
        }
    return report


def difficulty_preset(band: str) -> dict:
    from .preset_loader import difficulty_preset as load_difficulty_preset
    return load_difficulty_preset("maze", band)


def quality_rejection(difficulty: dict, requested_band: str = "medium") -> str | None:
    metrics = difficulty["mechanical"]
    preset = difficulty_preset(requested_band)
    difficulty["requested_band"] = requested_band
    difficulty["quality_preset"] = preset["name"]
    below = (
        metrics["solution_cost"] < preset["min_cost"]
        or metrics["decision_count"] < preset["min_decisions"]
        or metrics["false_leads"] < preset["min_false_leads"]
        or metrics["deep_false_leads"] < preset["min_deep_false_leads"]
        or metrics["goal_zone_false_leads"] < preset["min_goal_zone_false_leads"]
    )
    if below:
        return "TOO_TRIVIAL"
    above = (
        metrics["solution_cost"] > preset["max_cost"]
        or metrics["decision_count"] > preset["max_decisions"]
        or metrics["false_leads"] > preset["max_false_leads"]
        or metrics["deep_false_leads"] > preset["max_deep_false_leads"]
    )
    if above:
        return "ANIMATION_TOO_LONG"
    for metric, minimum in preset.get("metric_minimums", {}).items():
        if metrics[metric] < minimum:
            return "TOO_TRIVIAL"
    for metric, maximum in preset.get("metric_maximums", {}).items():
        if metrics[metric] > maximum:
            return "TOO_HARD"
    if requested_band == "target":
        recipe = difficulty.get("generation_traits")
        if recipe is None or len(recipe["active_traits"]) < 2:
            return "INVALID_TRAIT_RECIPE"
        if recipe["active_satisfied_count"] != len(recipe["active_traits"]):
            return "TRAIT_REQUIREMENTS_NOT_MET"
    difficulty["accepted_band"] = requested_band
    if requested_band in {"easy", "medium"}:
        difficulty["human"]["status"] = "uncalibrated-too-easy"
    elif requested_band == "target":
        difficulty["human"].update({
            "status": "calibrated-within-person-target",
            "model_version": "maze-single-evaluator-round2-2026-08-21",
            "calibration_scope": "single-evaluator-within-person",
            "target_rating": 3,
            "solve_before_reveal_observation": "borderline-variable",
        })
    return None
