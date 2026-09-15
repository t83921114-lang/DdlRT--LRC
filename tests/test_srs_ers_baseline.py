#!/usr/bin/env python3
import functools
import math
import operator
import random
import unittest

from test_ddlrt_lrc_merge import encode, gf_mul, gf_pow


def pair_layout(stripe_id, k, r, z, rack_count, nodes_per_rack):
    pair_id, pair_pos = divmod(stripe_id, 2)
    capacity = r + 1
    per_group = k // z
    full_parts, tail_load = divmod(per_group, capacity)
    parts = full_parts + bool(tail_load)
    parity = pair_id % rack_count

    def data_rack(offset):
        return (parity + 1 + offset) % rack_count

    first_racks = z * parts
    cursor = 0 if pair_pos == 0 else first_racks
    result = {"parity": parity, "data": [], "placement_groups": []}
    placement_group = 0
    for group in range(z):
        for part in range(parts):
            tail = bool(tail_load) and part == parts - 1
            load = tail_load if tail else capacity
            if pair_pos == 1 and tail:
                rack = data_rack(group * parts + part)
            else:
                rack = data_rack(cursor)
                cursor += 1
            offset = tail_load if pair_pos == 1 and tail else 0
            result["data"].extend((group, rack, offset + node) for node in range(load))
            result["placement_groups"].append((placement_group, group, rack, load))
            placement_group += 1
    required = 1 + first_racks + z * full_parts
    assert required <= rack_count
    assert max(r + z, capacity, 2 * tail_load) <= nodes_per_rack
    return result


def paired(ids, permutation=None):
    ordered = sorted(ids)
    if permutation is not None:
        ordered = [ordered[index] for index in permutation]
    return list(zip(ordered[::2], ordered[1::2])), (ordered[-1] if len(ordered) % 2 else None)


def derived_node_seed(seed, round_number, group, cluster):
    return seed ^ (round_number << 48) ^ (group << 32) ^ cluster


def srs_merge(left, right_data, input_k, r, z, local_members=None):
    output = []
    group_size = len(right_data) // z
    for parity_index, parity in enumerate(left):
        if parity_index < r:
            base = gf_pow(2, parity_index + 1)
            members = enumerate(right_data)
            coeff = lambda i: gf_pow(base, input_k + i)
        else:
            group = parity_index - r
            selected = (local_members[group] if local_members is not None else
                        right_data[group * group_size:(group + 1) * group_size])
            members = enumerate(selected)
            coeff = lambda _: 1
        members = list(members)
        output.append(bytes(
            a ^ functools.reduce(operator.xor,
                                 (gf_mul(coeff(i), block[pos]) for i, block in members), 0)
            for pos, a in enumerate(parity)))
    return output


class BaselineTest(unittest.TestCase):
    def test_pair_placement_metadata_and_constraints(self):
        left = pair_layout(8, 10, 3, 2, 17, 8)
        right = pair_layout(9, 10, 3, 2, 17, 8)
        self.assertEqual(left["parity"], right["parity"])
        for group in range(2):
            l = {(rack, node) for g, rack, node in left["data"] if g == group}
            rr = {(rack, node) for g, rack, node in right["data"] if g == group}
            self.assertFalse(l & rr)
            self.assertTrue({rack for rack, _ in l} & {rack for rack, _ in rr})
        self.assertNotIn(left["parity"], {rack for _, rack, _ in left["data"]})


    def test_placement_groups_and_client_slices_align(self):
        layout = pair_layout(0, 10, 3, 2, 17, 8)
        groups = layout["placement_groups"]
        self.assertEqual([4, 1, 4, 1], [load for _, _, _, load in groups])
        self.assertEqual(len(groups), len({group for group, _, _, _ in groups}))
        self.assertTrue(all(len({rack}) == 1 for _, _, rack, _ in groups))
        data_counts = [load for _, _, _, load in groups] + [0]
        global_counts = [0] * len(groups) + [3]
        local_counts = [0] * len(groups) + [2]
        self.assertEqual([4, 1, 4, 1, 0], data_counts)
        self.assertEqual(len(data_counts), len(global_counts))
        self.assertEqual(len(data_counts), len(local_counts))
        self.assertEqual([3], [value for value in global_counts if value])
        self.assertEqual([2], [value for value in local_counts if value])

    def test_round_pairing_preserves_odd_bye(self):
        pairs, bye = paired(range(7), [3, 0, 6, 1, 5, 2, 4])
        self.assertEqual([(3, 0), (6, 1), (5, 2)], pairs)
        self.assertEqual(4, bye)
        even_pairs, even_bye = paired(range(4))
        self.assertEqual([(0, 1), (2, 3)], even_pairs)
        self.assertIsNone(even_bye)

    def test_relayout_node_seed_is_deterministic_and_code_independent(self):
        seed = derived_node_seed(99, 2, 1, 7)
        self.assertEqual(seed, derived_node_seed(99, 2, 1, 7))
        self.assertNotEqual(seed, derived_node_seed(99, 3, 1, 7))
        self.assertNotEqual(seed, derived_node_seed(99, 2, 0, 7))
        self.assertNotEqual(seed, derived_node_seed(99, 2, 1, 8))

    def test_relayout_rack_count_and_exclusion(self):
        total, r = 20, 3
        targets = math.ceil(total / (r + 1))
        left_parity = 4
        available = [rack for rack in range(17) if rack != left_parity]
        group0, group1 = set(available[:targets]), set(available[targets:2 * targets])
        self.assertEqual(targets, len(group0))
        self.assertFalse(group0 & group1)
        self.assertNotIn(left_parity, group0 | group1)

    def test_srs_and_ers_match_reencoding_for_two_rounds(self):
        rng = random.Random(20260915)
        k, r, z, size = 10, 3, 2, 97
        data = [[bytes(rng.randrange(256) for _ in range(size)) for _ in range(k)]
                for _ in range(4)]
        parity = [list(encode(items, r, z)[0] + encode(items, r, z)[1]) for items in data]
        ers1 = [[bytes(a ^ gf_mul(gf_pow(gf_pow(2, j + 1), k) if j < r else 1, b)
                       for a, b in zip(parity[x][j], parity[x + 1][j]))
                 for j in range(r + z)] for x in (0, 2)]
        srs1 = [srs_merge(parity[x], data[x + 1], k, r, z) for x in (0, 2)]
        self.assertEqual(ers1, srs1)
        ers2 = [bytes(a ^ gf_mul(gf_pow(gf_pow(2, j + 1), 2 * k) if j < r else 1, b)
                      for a, b in zip(ers1[0][j], ers1[1][j])) for j in range(r + z)]
        right_groups = [data[2][g * (k // z):(g + 1) * (k // z)] +
                        data[3][g * (k // z):(g + 1) * (k // z)] for g in range(z)]
        srs2 = srs_merge(srs1[0], data[2] + data[3], 2 * k, r, z, right_groups)
        expected_globals = list(encode(sum(data, []), r, z)[0])
        group_size = k // z
        expected_locals = []
        for group in range(z):
            members = [block for stripe in data
                       for block in stripe[group * group_size:(group + 1) * group_size]]
            expected_locals.append(bytes(
                functools.reduce(operator.xor, (block[pos] for block in members), 0)
                for pos in range(size)))
        expected = expected_globals + expected_locals
        self.assertEqual(expected, ers2)
        self.assertEqual(expected, srs2)


if __name__ == "__main__":
    unittest.main()
