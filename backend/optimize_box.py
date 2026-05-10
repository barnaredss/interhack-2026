import numpy as np
import random
import math


class _Instance:
    """A multi-cell rectangular item placed in the truck lattice."""
    __slots__ = ("id", "client", "item_type", "is_return", "anchor", "shape")

    def __init__(self, instance_id, client, item_type, is_return, anchor, shape):
        self.id = instance_id
        self.client = client
        self.item_type = item_type
        self.is_return = is_return
        self.anchor = anchor      # (x, y, z) min corner
        self.shape = shape        # (lx, ly, lz)

    def cells_at(self, anchor):
        x0, y0, z0 = anchor
        lx, ly, lz = self.shape
        out = []
        for i in range(lx):
            for j in range(ly):
                for k in range(lz):
                    out.append((x0 + i, y0 + j, z0 + k))
        return out

    @property
    def cells(self):
        return self.cells_at(self.anchor)


class SmartTruckOptimizer3D:
    EMPTY_KEG = -1  # post-return solid block; structural support but always blocks extraction

    def __init__(self, length_bays, width_pallets, height_layers, route, item_shapes=None):
        """
        length_bays:    lateral bays (X-axis)
        width_pallets:  depth of each bay (Y-axis); both Y-walls are open along the full length
        height_layers:  max stacking height (Z-axis)
        route:          client IDs in delivery order
        item_shapes:    dict[item_type, (lx, ly, lz)] of rectangular box dimensions.
                        Defaults to {"keg": (1, 1, 1)} for backward compatibility.

        Removability rule: an instance is removable iff at least one of its three sides
        — top, left (-Y), right (+Y) — is fully clear of blockers (other-client items
        or empty kegs). The truck has no front/back access.
        """
        self.L = length_bays
        self.W = width_pallets
        self.H = height_layers
        self.route = list(route)

        shapes = item_shapes if item_shapes is not None else {"keg": (1, 1, 1)}
        self.item_shapes = {}
        for name, dims in shapes.items():
            dims = tuple(int(d) for d in dims)
            if len(dims) != 3 or any(d < 1 for d in dims):
                raise ValueError(f"Invalid shape for item '{name}': {dims}")
            if dims[0] > self.L or dims[1] > self.W or dims[2] > self.H:
                raise ValueError(f"Item '{name}' shape {dims} exceeds grid {(self.L, self.W, self.H)}.")
            self.item_shapes[name] = dims

        self._instances = {}
        self._next_id = 1

        # Work caches (variance over cell-x; multi-cell items contribute each cell once)
        self._x_sum = {}
        self._x_sq_sum = {}
        self._counts = {}
        self._max_var = max(((self.L - 1) ** 2) / 4.0, 1e-9)

    # ---------- helpers ----------

    def _is_blocker(self, value, client):
        """True iff a cell with `value` blocks `client` from extracting through it."""
        if value == 0:
            return False
        if value == self.EMPTY_KEG:
            return True
        return self._instances[value].client != client

    # ---------- initial state ----------

    def generate_initial_state(self, client_item_counts, client_returns):
        """
        client_item_counts: {client_id: {item_type: total_count}}
        client_returns:     {client_id: {item_type: returns_count}}

        For each (client, item_type) we create `total - returns` pure-delivery instances
        and `returns` substitution instances. Instances are placed at random valid
        anchors; raises if the grid is too crowded.
        """
        self._instances = {}
        self._next_id = 1
        state = np.zeros((self.L, self.W, self.H), dtype=int)

        pending = []  # (client, item_type, is_return, shape)
        for client, items in client_item_counts.items():
            returns_for_client = client_returns.get(client, {})
            for item_type, count in items.items():
                if item_type not in self.item_shapes:
                    raise KeyError(f"Unknown item_type '{item_type}' (not in item_shapes).")
                num_returns = returns_for_client.get(item_type, 0)
                num_pure = count - num_returns
                if num_pure < 0:
                    raise ValueError(
                        f"Client {client} item '{item_type}': returns ({num_returns}) "
                        f"exceed total ({count})."
                    )
                shape = self.item_shapes[item_type]
                for _ in range(num_pure):
                    pending.append((client, item_type, False, shape))
                for _ in range(num_returns):
                    pending.append((client, item_type, True, shape))

        total_cells_needed = sum(s[0] * s[1] * s[2] for *_, s in pending)
        capacity = self.L * self.W * self.H
        if total_cells_needed > capacity:
            raise ValueError(f"Items need {total_cells_needed} cells; grid holds {capacity}.")

        random.shuffle(pending)
        for client, item_type, is_return, shape in pending:
            anchor = self._place_random(state, shape)
            if anchor is None:
                raise RuntimeError(
                    f"Could not place item '{item_type}' for client {client}; grid too crowded."
                )
            inst_id = self._next_id
            self._next_id += 1
            inst = _Instance(inst_id, client, item_type, is_return, anchor, shape)
            self._instances[inst_id] = inst
            for (x, y, z) in inst.cells:
                state[x, y, z] = inst_id
        return state

    def _place_random(self, state, shape, max_tries=500):
        lx, ly, lz = shape
        for _ in range(max_tries):
            x = random.randint(0, self.L - lx)
            y = random.randint(0, self.W - ly)
            z = random.randint(0, self.H - lz)
            if self._cells_clear(state, x, y, z, lx, ly, lz):
                return (x, y, z)
        # Deterministic fallback scan
        for x in range(self.L - lx + 1):
            for y in range(self.W - ly + 1):
                for z in range(self.H - lz + 1):
                    if self._cells_clear(state, x, y, z, lx, ly, lz):
                        return (x, y, z)
        return None

    def _cells_clear(self, state, x, y, z, lx, ly, lz, allow_id=0):
        """True iff the box at (x,y,z) of size (lx,ly,lz) contains only 0 or `allow_id`."""
        for i in range(lx):
            for j in range(ly):
                for k in range(lz):
                    v = state[x + i, y + j, z + k]
                    if v != 0 and v != allow_id:
                        return False
        return True

    # ---------- penalty (full eval) ----------

    def physical_penalty(self, state):
        """
        Sum of:
          1. Initial gravity violations (cells whose support is void)
          2. For each client in route order:
              a. Extraction violations: instances with no fully-clear side
              b. Post-ablation gravity violations
        """
        penalty = 0
        truck = np.copy(state)

        penalty += self._gravity_violations(truck)

        # Group instances by client once
        by_client = {c: [] for c in self.route}
        for inst in self._instances.values():
            if inst.client in by_client:
                by_client[inst.client].append(inst)

        for client in self.route:
            for inst in by_client[client]:
                if self._instance_blocked(truck, inst):
                    penalty += 1
            for inst in by_client[client]:
                fill = self.EMPTY_KEG if inst.is_return else 0
                for (x, y, z) in inst.cells:
                    truck[x, y, z] = fill
            penalty += self._gravity_violations(truck)

        return penalty

    def _gravity_violations(self, truck):
        if self.H < 2:
            return 0
        upper = truck[:, :, 1:]
        lower = truck[:, :, :-1]
        return int(np.sum((upper > 0) & (lower == 0)))

    def _instance_blocked(self, truck, inst):
        """True iff none of {top, left, right} sides is fully clear of blockers."""
        x0, y0, z0 = inst.anchor
        lx, ly, lz = inst.shape
        c = inst.client

        # Top side: above the item
        if z0 + lz >= self.H:
            return False  # extends to roof, top is open by definition
        if self._region_clear(truck, x0, x0 + lx, y0, y0 + ly, z0 + lz, self.H, c):
            return False

        # Left side
        if y0 == 0:
            return False
        if self._region_clear(truck, x0, x0 + lx, 0, y0, z0, z0 + lz, c):
            return False

        # Right side
        if y0 + ly >= self.W:
            return False
        if self._region_clear(truck, x0, x0 + lx, y0 + ly, self.W, z0, z0 + lz, c):
            return False

        return True

    def _region_clear(self, truck, x_lo, x_hi, y_lo, y_hi, z_lo, z_hi, client):
        for x in range(x_lo, x_hi):
            for y in range(y_lo, y_hi):
                for z in range(z_lo, z_hi):
                    if self._is_blocker(truck[x, y, z], client):
                        return False
        return True

    # ---------- delta penalty ----------

    def _instances_reading_cells(self, changed_cells):
        """Set of instance IDs whose blocking computation reads any of `changed_cells`."""
        affected = set()
        for inst_id, inst in self._instances.items():
            x0, y0, z0 = inst.anchor
            lx, ly, lz = inst.shape
            for (cx, cy, cz) in changed_cells:
                in_x = x0 <= cx < x0 + lx
                in_y = y0 <= cy < y0 + ly
                in_z = z0 <= cz < z0 + lz
                if in_x and (
                    (in_y and cz >= z0 + lz) or            # top column
                    (cy < y0 and in_z) or                  # left row
                    (cy >= y0 + ly and in_z) or            # right row
                    (in_y and in_z)                        # inside (shouldn't happen unless self)
                ):
                    affected.add(inst_id)
                    break
        return affected

    def _evaluate_local_penalty(self, state, changed_cells, affected_ids):
        """
        Sum penalty contributions whose values can differ between current and proposed
        states. Cells outside `changed_cells` and instances outside `affected_ids` give
        identical contributions in both, so they cancel in the delta.

        The cascade still mutates a full-state copy (ablation is global), but only the
        listed instances' extraction blocks and only the gravity neighbors of changed
        cells contribute to the returned sum.
        """
        truck = np.copy(state)

        gravity_cells = set()
        for (x, y, z) in changed_cells:
            gravity_cells.add((x, y, z))
            if z + 1 < self.H:
                gravity_cells.add((x, y, z + 1))

        penalty = 0
        for (x, y, z) in gravity_cells:
            if z >= 1 and truck[x, y, z] > 0 and truck[x, y, z - 1] == 0:
                penalty += 1

        by_client = {c: [] for c in self.route}
        for inst in self._instances.values():
            if inst.client in by_client:
                by_client[inst.client].append(inst)

        for client in self.route:
            for inst in by_client[client]:
                if inst.id in affected_ids and self._instance_blocked(truck, inst):
                    penalty += 1
            for inst in by_client[client]:
                fill = self.EMPTY_KEG if inst.is_return else 0
                for (x, y, z) in inst.cells:
                    truck[x, y, z] = fill
            for (x, y, z) in gravity_cells:
                if z >= 1 and truck[x, y, z] > 0 and truck[x, y, z - 1] == 0:
                    penalty += 1

        return penalty

    # ---------- work ----------

    def spatial_work(self, state):
        """
        Mean-normalized variance of x-coords per client, in roughly [0, 1].
        Each cell of a multi-cell item contributes its x once; rewards keeping a
        client's items clustered along the truck length so the driver walks less
        per stop. Translation-invariant (cluster *position* doesn't matter — both
        Y-sides are open, see project notes).
        """
        if not self.route:
            return 0.0
        total = 0.0
        for client in self.route:
            xs = []
            for inst in self._instances.values():
                if inst.client == client:
                    x0, _, _ = inst.anchor
                    lx, ly, lz = inst.shape
                    for i in range(lx):
                        xs.extend([x0 + i] * (ly * lz))
            if len(xs) > 1:
                total += float(np.var(xs))
        return total / (self._max_var * len(self.route))

    def _init_work_cache(self, state):
        self._x_sum = {c: 0 for c in self.route}
        self._x_sq_sum = {c: 0 for c in self.route}
        self._counts = {c: 0 for c in self.route}
        for inst in self._instances.values():
            if inst.client not in self._counts:
                continue
            for (x, _, _) in inst.cells:
                self._x_sum[inst.client] += x
                self._x_sq_sum[inst.client] += x * x
                self._counts[inst.client] += 1

    def _work_from_cache(self):
        if not self.route:
            return 0.0
        total = 0.0
        for client in self.route:
            n = self._counts.get(client, 0)
            if n > 1:
                mean = self._x_sum[client] / n
                total += self._x_sq_sum[client] / n - mean * mean
        return total / (self._max_var * len(self.route))

    def _update_work_cache_for_move(self, inst, old_anchor, new_anchor):
        """Reflect that `inst` translated from old_anchor to new_anchor. O(|cells|)."""
        if inst.client not in self._counts:
            return
        for (x, _, _) in inst.cells_at(old_anchor):
            self._x_sum[inst.client] -= x
            self._x_sq_sum[inst.client] -= x * x
        for (x, _, _) in inst.cells_at(new_anchor):
            self._x_sum[inst.client] += x
            self._x_sq_sum[inst.client] += x * x
        # counts unchanged

    # ---------- optimize ----------

    def optimize(self, initial_state, steps=40000, seed=None,
                 T_0=1.0, T_min=1e-4, gamma_0=0.01, gamma_max=100.0):
        """
        SA with single-instance translation proposals + delta penalty evaluation.

        Each step picks a random instance, picks a random in-bounds anchor, and
        accepts/rejects a translation by Metropolis criterion on H = W + γP. Same-anchor
        proposals and proposals that overlap other items are skipped without scoring.
        """
        if seed is not None:
            random.seed(seed)
            np.random.seed(seed)

        current_state = np.copy(initial_state)
        self._init_work_cache(current_state)
        current_P = self.physical_penalty(current_state)
        current_W = self._work_from_cache()
        current_H = current_W + gamma_max * current_P

        best_state = np.copy(current_state)
        best_H = current_H
        best_feasible_state = None
        best_feasible_W = float('inf')

        instance_ids = list(self._instances.keys())
        if not instance_ids:
            return current_state, current_P, current_W

        for step in range(steps):
            fraction = step / float(steps)
            T = T_0 * ((T_min / T_0) ** fraction)
            gamma = gamma_0 + (gamma_max - gamma_0) * (fraction ** 4)

            inst_id = random.choice(instance_ids)
            inst = self._instances[inst_id]
            lx, ly, lz = inst.shape

            x_new = random.randint(0, self.L - lx)
            y_new = random.randint(0, self.W - ly)
            z_new = random.randint(0, self.H - lz)
            new_anchor = (x_new, y_new, z_new)
            old_anchor = inst.anchor

            if new_anchor == old_anchor:
                continue

            # Validate: new footprint is void or self-owned
            if not self._cells_clear(current_state, x_new, y_new, z_new, lx, ly, lz, allow_id=inst_id):
                continue

            old_cells = inst.cells_at(old_anchor)
            new_cells = inst.cells_at(new_anchor)
            changed_cells = set(old_cells) | set(new_cells)
            affected_ids = self._instances_reading_cells(changed_cells)
            affected_ids.add(inst_id)

            # Score before
            before_local = self._evaluate_local_penalty(current_state, changed_cells, affected_ids)

            # Apply move
            for (x, y, z) in old_cells:
                current_state[x, y, z] = 0
            for (x, y, z) in new_cells:
                current_state[x, y, z] = inst_id
            inst.anchor = new_anchor
            self._update_work_cache_for_move(inst, old_anchor, new_anchor)

            # Score after (note: instance.anchor is now new_anchor, so its blocking is
            # evaluated at the new position; affected_ids set doesn't change because it
            # already included this instance.)
            after_local = self._evaluate_local_penalty(current_state, changed_cells, affected_ids)
            delta_P = after_local - before_local
            proposed_P = current_P + delta_P
            proposed_W = self._work_from_cache()
            delta_W = proposed_W - current_W
            delta_H = delta_W + gamma * delta_P

            if delta_H < 0 or random.random() < math.exp(-delta_H / max(T, 1e-12)):
                current_P = proposed_P
                current_W = proposed_W
                current_H = current_W + gamma_max * current_P
                if current_H < best_H:
                    best_H = current_H
                    best_state = np.copy(current_state)
                if current_P == 0 and current_W < best_feasible_W:
                    best_feasible_W = current_W
                    best_feasible_state = np.copy(current_state)
            else:
                # Revert
                for (x, y, z) in new_cells:
                    current_state[x, y, z] = 0
                for (x, y, z) in old_cells:
                    current_state[x, y, z] = inst_id
                inst.anchor = old_anchor
                self._update_work_cache_for_move(inst, new_anchor, old_anchor)

        result_state = best_feasible_state if best_feasible_state is not None else best_state
        # Sync instance anchors to the returned state — best_state was np.copy'd at peak
        # quality, but self._instances has been moving along with current_state since.
        self._sync_instances_to_state(result_state)
        return result_state, self.physical_penalty(result_state), self.spatial_work(result_state)

    # ---------- result inspection ----------

    def _sync_instances_to_state(self, state):
        """Set each instance's anchor to its min-corner in `state`. Drops instances
        whose cells aren't found (shouldn't happen for an internally produced state)."""
        missing = []
        for inst_id, inst in self._instances.items():
            cells = np.argwhere(state == inst_id)
            if len(cells) == 0:
                missing.append(inst_id)
                continue
            mn = cells.min(axis=0)
            inst.anchor = (int(mn[0]), int(mn[1]), int(mn[2]))
        for inst_id in missing:
            del self._instances[inst_id]

    def get_layout(self, state=None):
        """
        Return the optimized placement as a list of dicts, one per instance:
            {id, client, item_type, is_return, anchor, shape, cells}

        With no argument, reads the current synced anchors (set by optimize()).
        Pass a state array to derive positions directly from it instead.
        """
        layout = []
        if state is None:
            for inst in self._instances.values():
                layout.append({
                    "id": inst.id,
                    "client": inst.client,
                    "item_type": inst.item_type,
                    "is_return": inst.is_return,
                    "anchor": inst.anchor,
                    "shape": inst.shape,
                    "cells": inst.cells,
                })
        else:
            for inst in self._instances.values():
                cells = np.argwhere(state == inst.id)
                if len(cells) == 0:
                    continue
                anchor = tuple(int(v) for v in cells.min(axis=0))
                layout.append({
                    "id": inst.id,
                    "client": inst.client,
                    "item_type": inst.item_type,
                    "is_return": inst.is_return,
                    "anchor": anchor,
                    "shape": inst.shape,
                    "cells": [tuple(int(v) for v in c) for c in cells],
                })
        return layout


# --- EXECUTION ---
if __name__ == "__main__":
    optimizer = SmartTruckOptimizer3D(
        length_bays=8, width_pallets=4, height_layers=3, route=[1, 2, 3],
        item_shapes={
            "keg":   (1, 1, 1),
            "crate": (2, 1, 1),
            "tower": (1, 1, 2),
        },
    )

    # {client: {item_type: total_count}}
    orders = {
        1: {"keg": 12, "crate": 4},
        2: {"keg": 18, "crate": 6},
        3: {"keg": 15, "tower": 4},
    }
    # {client: {item_type: returns_count}}  — must be ≤ total per (client, item)
    returns = {
        1: {"keg": 8},
        2: {"keg": 5},
        3: {"keg": 10},
    }

    initial = optimizer.generate_initial_state(orders, returns)
    print("Initial 3D Penalty:        ", optimizer.physical_penalty(initial))
    print(f"Initial Work (normalized): {optimizer.spatial_work(initial):.4f}")

    final_state, final_P, final_W = optimizer.optimize(initial, steps=30000)

    print(f"\nOptimization Complete.")
    print(f"Final Physical Violations: {final_P}")
    print(f"Final Work Metric:         {final_W:.4f}")

    if final_P == 0:
        print("\nValid 3D Lattice Layout generated successfully.")
    else:
        print("\nOptimizer trapped in local minimum. Try increasing 'steps'.")

    layout = optimizer.get_layout()
    print(f"\nLayout: {len(layout)} items")
    for item in sorted(layout, key=lambda i: (i["client"], i["anchor"])):
        kind = "return" if item["is_return"] else "delivery"
        print(f"  client={item['client']} {item['item_type']:<6} {kind:<8} "
              f"anchor={item['anchor']} shape={item['shape']}")




'''
  Input format for generate_initial_state:
  orders  = {client_id: {item_type: total_count}}
  returns = {client_id: {item_type: returns_count}}
'''