#!/usr/bin/env python3
import math
import unittest


def racks(level, k, r, z):
    return z * math.ceil((2**level * (k // z)) / (r + 1)) + 1


def merge_parameters(k, r, z, cluster_num=17):
    values = []
    previous = racks(0, k, r, z)
    level = 1
    while True:
        current = racks(level, k, r, z)
        shared = 2 * previous - current
        if current > cluster_num or shared not in (1, 1 + z):
            break
        values.append(shared)
        previous = current
        level += 1
    return len(values), values


def logical_layout(k, r, z, rounds, shared_values):
    batch_size = 2**rounds
    parent = list(range(batch_size * z))

    def root(value):
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def unite(left, right):
        left, right = root(left), root(right)
        if left != right:
            parent[right] = left

    super_tails = [[pos * z + group for group in range(z)]
                   for pos in range(batch_size)]
    for shared in shared_values:
        next_tails = []
        for pair in range(len(super_tails) // 2):
            row = []
            for group in range(z):
                left = super_tails[2 * pair][group]
                right = super_tails[2 * pair + 1][group]
                if shared == 1 + z:
                    unite(left, right)
                row.append(right)
            next_tails.append(row)
        super_tails = next_tails

    tail_roots = {root(pos * z + group)
                  for pos in range(batch_size) for group in range(z)}
    data_per_group = k // z
    rack_capacity = r + 1
    racks_per_group = math.ceil(data_per_group / rack_capacity)
    full_racks = batch_size * z * (racks_per_group - 1)
    return 1 + len(tail_roots) + full_racks, parent, root


class DdlRTLrcLayoutTest(unittest.TestCase):
    def check_case(self, k, z, r, expected_rounds, expected_s):
        rounds, shared_values = merge_parameters(k, r, z)
        self.assertEqual(expected_rounds, rounds)
        self.assertEqual(expected_s, shared_values)
        logical_racks, _, _ = logical_layout(k, r, z, rounds, shared_values)
        self.assertEqual(racks(rounds, k, r, z), logical_racks)
        self.assertLessEqual(logical_racks, 17)
        self.assertLessEqual(max(r + z, r + 1), 8)

    def test_six_two_two(self):
        self.check_case(6, 2, 2, 3, [1, 1, 1])

    def test_ten_two_three(self):
        self.check_case(10, 2, 3, 2, [3, 3])

    def test_twelve_two_two(self):
        self.check_case(12, 2, 2, 2, [1, 1])

    def test_shared_tail_capacity(self):
        k, z, r = 10, 2, 3
        rounds, shared_values = merge_parameters(k, r, z)
        _, _, root = logical_layout(k, r, z, rounds, shared_values)
        tail_load = (k // z) % (r + 1)
        if tail_load == 0:
            tail_load = r + 1
        for group in range(z):
            counts = {}
            for pos in range(2**rounds):
                key = root(pos * z + group)
                counts[key] = counts.get(key, 0) + tail_load
            self.assertTrue(all(load <= r + 1 for load in counts.values()))


if __name__ == "__main__":
    unittest.main()
