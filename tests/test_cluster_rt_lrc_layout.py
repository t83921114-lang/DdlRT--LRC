#!/usr/bin/env python3
import math
import random
import unittest


def required_data_racks(k, r, z):
    if k <= 0 or r < 1 or z < 1 or k % z:
        raise ValueError("invalid ClusterRT_LRC parameters")
    return z * math.ceil((k // z) / (r + 1))


def placement(k, r, z, cluster_num, nodes_per_cluster, stripe_id, seed):
    data_rack_count = required_data_racks(k, r, z)
    if nodes_per_cluster < max(r + z, r + 1):
        raise ValueError("not enough nodes")
    if cluster_num < data_rack_count + 1:
        raise ValueError("not enough racks")

    parity_rack = stripe_id % cluster_num
    # Python's PRNG is not expected to match mt19937/seed_seq byte for byte;
    # this model verifies the deterministic contract and placement invariants.
    rng = random.Random((seed, stripe_id, k, r, z, cluster_num, nodes_per_cluster).__repr__())
    candidates = [rack for rack in range(cluster_num) if rack != parity_rack]
    rng.shuffle(candidates)
    data_racks = candidates[:data_rack_count]

    groups = []
    data_per_local_group = k // z
    next_rack = 0
    for local_group in range(z):
        first = local_group * data_per_local_group
        remaining = data_per_local_group
        while remaining:
            load = min(remaining, r + 1)
            nodes = list(range(nodes_per_cluster))
            rng.shuffle(nodes)
            groups.append({
                "local_group": local_group,
                "rack": data_racks[next_rack],
                "blocks": list(range(first, first + load)),
                "nodes": nodes[:load],
            })
            first += load
            remaining -= load
            next_rack += 1

    parity_nodes = list(range(nodes_per_cluster))
    rng.shuffle(parity_nodes)
    parity = {
        "rack": parity_rack,
        "global": list(range(k, k + r)),
        "local": list(range(k + r, k + r + z)),
        "nodes": parity_nodes[:r + z],
    }
    return groups, parity


class ClusterRTLrcLayoutTest(unittest.TestCase):
    def check_layout(self, k, r, z, cluster_num=17, nodes=8, stripe_id=0, seed=7):
        groups, parity = placement(k, r, z, cluster_num, nodes, stripe_id, seed)
        self.assertEqual(stripe_id % cluster_num, parity["rack"])
        self.assertEqual(r + z, len(parity["nodes"]))
        self.assertEqual(r + z, len(set(parity["nodes"])))

        data_blocks = []
        data_racks = []
        for group in groups:
            self.assertLessEqual(len(group["blocks"]), r + 1)
            self.assertEqual(len(group["nodes"]), len(set(group["nodes"])))
            self.assertNotEqual(parity["rack"], group["rack"])
            expected_local_group = group["blocks"][0] // (k // z)
            self.assertEqual(expected_local_group, group["local_group"])
            data_blocks.extend(group["blocks"])
            data_racks.append(group["rack"])
        self.assertEqual(list(range(k)), data_blocks)
        self.assertEqual(len(data_racks), len(set(data_racks)))
        self.assertEqual(list(range(k, k + r)), parity["global"])
        self.assertEqual(list(range(k + r, k + r + z)), parity["local"])
        return groups, parity

    def test_full_and_tail_groups(self):
        groups, _ = self.check_layout(k=10, r=3, z=2)
        self.assertEqual([4, 1, 4, 1], [len(group["blocks"]) for group in groups])

    def test_exact_groups(self):
        groups, _ = self.check_layout(k=16, r=3, z=2)
        self.assertEqual([4, 4, 4, 4], [len(group["blocks"]) for group in groups])

    def test_seed_is_reproducible(self):
        first = placement(10, 3, 2, 17, 8, 4, 1234)
        second = placement(10, 3, 2, 17, 8, 4, 1234)
        other = placement(10, 3, 2, 17, 8, 4, 1235)
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)

    def test_parity_rack_round_robin(self):
        racks = [placement(10, 3, 2, 17, 8, stripe, 0)[1]["rack"]
                 for stripe in range(19)]
        self.assertEqual(list(range(17)) + [0, 1], racks)

    def test_rejects_insufficient_capacity(self):
        with self.assertRaises(ValueError):
            placement(10, 3, 2, 4, 8, 0, 0)
        with self.assertRaises(ValueError):
            placement(10, 3, 2, 17, 4, 0, 0)
        with self.assertRaises(ValueError):
            placement(9, 3, 2, 17, 8, 0, 0)


if __name__ == "__main__":
    unittest.main()
