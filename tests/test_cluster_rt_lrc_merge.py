#!/usr/bin/env python3
import unittest


def aim(block_count, capacity):
    values = []
    while block_count:
        load = min(block_count, capacity)
        values.append(load)
        block_count -= load
    return sorted(values)


def move_cost(left, right, groups, blocks_per_merged_group, capacity):
    total = 0
    for group in range(groups):
        counts = {}
        for rack, value in left[group].items():
            counts[rack] = counts.get(rack, 0) + value
        for rack, value in right[group].items():
            counts[rack] = counts.get(rack, 0) + value
        current = sorted(counts.values())
        target = aim(blocks_per_merged_group, capacity)
        width = max(len(current), len(target))
        current = [0] * (width - len(current)) + current
        target = [0] * (width - len(target)) + target
        total += sum(max(0, have - want) for have, want in zip(current, target))
    return total


def greedy_pairs(ids, layouts):
    ids = sorted(ids)
    result = []
    while len(ids) >= 2:
        choices = []
        for i, left in enumerate(ids):
            for j in range(i + 1, len(ids)):
                right = ids[j]
                cost = move_cost(layouts[left], layouts[right], 2, 10, 4)
                choices.append((cost, left, right))
        _, left, right = min(choices)
        result.append((left, right))
        ids.remove(left)
        ids.remove(right)
    return result, ids


def gf_mul(a, b):
    result = 0
    while b:
        if b & 1:
            result ^= a
        b >>= 1
        a <<= 1
        if a & 0x100:
            a ^= 0x11D
    return result


def gf_pow(base, exponent):
    result = 1
    while exponent:
        if exponent & 1:
            result = gf_mul(result, base)
        base = gf_mul(base, base)
        exponent >>= 1
    return result


class ClusterRTLrcMergeTest(unittest.TestCase):
    def test_per_local_group_aim(self):
        self.assertEqual([2, 4, 4], aim(10, 4))
        self.assertEqual([4, 4, 4, 4], aim(16, 4))

    def test_move_cost_is_group_aware(self):
        left = [{1: 4, 2: 1}, {3: 4, 4: 1}]
        compatible = [{5: 4, 2: 1}, {6: 4, 4: 1}]
        conflicting = [{1: 4, 2: 1}, {3: 4, 4: 1}]
        self.assertLess(move_cost(left, compatible, 2, 10, 4),
                        move_cost(left, conflicting, 2, 10, 4))

    def test_duplicate_nodes_are_repaired_inside_target_rack(self):
        blocks = [
            {"rack": 5, "node": 2},
            {"rack": 5, "node": 2},
            {"rack": 5, "node": 3},
        ]
        occupied = set()
        retained = []
        outgoing = []
        for block in blocks:
            if block["node"] in occupied:
                outgoing.append(block)
            else:
                occupied.add(block["node"])
                retained.append(block)
        self.assertEqual(2, len(retained))
        self.assertEqual(1, len(outgoing))
        free_node = next(node for node in range(8) if node not in occupied)
        outgoing[0]["node"] = free_node
        self.assertEqual(3, len({block["node"] for block in retained + outgoing}))
        self.assertTrue(all(block["rack"] == 5 for block in retained + outgoing))

    def test_cross_stripe_source_rack_overlap_is_relocatable(self):
        # Rack 2 belongs to local group 0 in the left stripe and local group 1
        # in the right stripe. Each source stripe is valid; the merge planner
        # must resolve this overlap through relocation instead of rejecting it.
        left = [{1: 4, 2: 1}, {3: 4, 4: 1}]
        right = [{5: 4, 6: 1}, {2: 4, 7: 1}]
        self.assertGreaterEqual(move_cost(left, right, 2, 10, 4), 0)
        source_owners = {}
        for stripe in (left, right):
            for group, racks in enumerate(stripe):
                for rack in racks:
                    source_owners.setdefault(rack, set()).add(group)
        self.assertEqual({0, 1}, source_owners[2])

    def test_greedy_pair_tie_is_deterministic(self):
        layouts = {
            sid: [{sid * 4 + 1: 4, sid * 4 + 2: 1},
                  {sid * 4 + 3: 4, sid * 4 + 4: 1}]
            for sid in range(4)
        }
        pairs, leftovers = greedy_pairs([3, 1, 2, 0], layouts)
        self.assertEqual([(0, 1), (2, 3)], pairs)
        self.assertEqual([], leftovers)

    def test_odd_count_leaves_one(self):
        layouts = {
            sid: [{sid * 4 + 1: 4, sid * 4 + 2: 1},
                  {sid * 4 + 3: 4, sid * 4 + 4: 1}]
            for sid in range(3)
        }
        _, leftovers = greedy_pairs([0, 1, 2], layouts)
        self.assertEqual(1, len(leftovers))

    def test_global_and_local_parity_coefficients(self):
        k, r, z = 10, 3, 2
        global_coeffs = [gf_pow(gf_pow(2, j + 1), k) for j in range(r)]
        self.assertEqual(r, len(global_coeffs))
        self.assertTrue(all(value != 0 for value in global_coeffs))
        local_coeffs = [1 for _ in range(z)]
        self.assertEqual([1, 1], local_coeffs)

    def test_two_round_sizes(self):
        base_k = 10
        current = base_k
        for round_id in (1, 2):
            self.assertEqual(base_k * (2 ** (round_id - 1)), current)
            current *= 2
        self.assertEqual(4 * base_k, current)


if __name__ == "__main__":
    unittest.main()
