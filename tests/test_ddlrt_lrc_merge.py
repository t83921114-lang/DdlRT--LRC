#!/usr/bin/env python3
import functools
import operator
import random
import unittest

POLY = 0x1D


def gf_mul(a, b):
    value = 0
    for _ in range(8):
        if b & 1:
            value ^= a
        carry = a & 0x80
        a = (a << 1) & 0xFF
        if carry:
            a ^= POLY
        b >>= 1
    return value


def gf_pow(base, exponent):
    result = 1
    while exponent:
        if exponent & 1:
            result = gf_mul(result, base)
        base = gf_mul(base, base)
        exponent >>= 1
    return result


def encode(data, r, z):
    k = len(data)
    globals_ = []
    for parity_index in range(r):
        base = gf_pow(2, parity_index + 1)
        globals_.append(bytes(
            functools.reduce(operator.xor,
                             (gf_mul(gf_pow(base, col), block[i]) for col, block in enumerate(data)),
                             0)
            for i in range(len(data[0]))
        ))
    group_size = k // z
    locals_ = []
    for group in range(z):
        members = data[group * group_size:(group + 1) * group_size]
        locals_.append(bytes(
            functools.reduce(operator.xor, (block[i] for block in members), 0)
            for i in range(len(data[0]))
        ))
    return globals_, locals_


def merge(left, right, input_k, r):
    output = []
    for index, (a, b) in enumerate(zip(left, right)):
        coeff = gf_pow(gf_pow(2, index + 1), input_k) if index < r else 1
        output.append(bytes(x ^ gf_mul(coeff, y) for x, y in zip(a, b)))
    return output


class DdlRTLrcMergeTest(unittest.TestCase):
    def test_global_parity_matches_reencoding_across_two_rounds(self):
        rng = random.Random(20260914)
        k, r, z, block_size = 10, 3, 2, 257
        stripes = []
        all_data = []
        for _ in range(4):
            data = [bytes(rng.randrange(256) for _ in range(block_size)) for _ in range(k)]
            all_data.append(data)
            globals_, locals_ = encode(data, r, z)
            stripes.append(globals_ + locals_)
        level1 = [merge(stripes[0], stripes[1], k, r),
                  merge(stripes[2], stripes[3], k, r)]
        level2 = merge(level1[0], level1[1], 2 * k, r)
        expected_globals, _ = encode(sum(all_data, []), r, z)
        self.assertEqual(expected_globals, level2[:r])

    def test_local_parity_is_same_group_union(self):
        rng = random.Random(17)
        k, r, z, block_size = 10, 3, 2, 129
        left_data = [bytes(rng.randrange(256) for _ in range(block_size)) for _ in range(k)]
        right_data = [bytes(rng.randrange(256) for _ in range(block_size)) for _ in range(k)]
        left_global, left_local = encode(left_data, r, z)
        right_global, right_local = encode(right_data, r, z)
        merged = merge(left_global + left_local, right_global + right_local, k, r)
        group_size = k // z
        expected_local = []
        for group in range(z):
            members = (left_data[group * group_size:(group + 1) * group_size] +
                       right_data[group * group_size:(group + 1) * group_size])
            expected_local.append(bytes(
                functools.reduce(operator.xor, (block[i] for block in members), 0)
                for i in range(block_size)))
        self.assertEqual(expected_local, merged[r:])

    def test_coefficients_change_with_input_k(self):
        first = [gf_pow(gf_pow(2, j + 1), 10) for j in range(3)]
        second = [gf_pow(gf_pow(2, j + 1), 20) for j in range(3)]
        self.assertEqual([0x74, 0xB4, 0x60], first)
        self.assertEqual([0xB4, 0x6A, 0xB9], second)

    def test_right_only_rebalance_prefers_conflict(self):
        capacity = 4
        left = [(1, "A"), (2, "B")]
        right = [(2, "C"), (3, "D")]
        selected = [block for node, block in right if node in {node for node, _ in left}]
        required = max(0, len(left) + len(right) - capacity)
        for _, block in right:
            if len(selected) >= required:
                break
            if block not in selected:
                selected.append(block)
        self.assertEqual(["C"], selected)


if __name__ == "__main__":
    unittest.main()
