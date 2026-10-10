"""嵌入结果的值与维度合同，供独立向量消费者复用。"""
import math


def validate_vectors(values, count, dimension=None):
    if len(values) != count:
        raise ValueError('embedding result count differs from input')
    expected = dimension
    for vector in values:
        if not vector or any(type(value) not in (int, float) or not math.isfinite(value) for value in vector):
            raise ValueError('invalid embedding vector')
        expected = len(vector) if expected is None else expected
        if len(vector) != expected:
            raise ValueError('embedding dimension differs')
    return expected
