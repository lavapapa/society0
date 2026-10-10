import os
from pathlib import Path
import subprocess
import sys

import pytest


def test_embedding_validation_is_independent_of_memory():
    from society0.kernel.vectors import validate_vectors

    assert validate_vectors([[1., 0.], [0., 1.]], 2) == 2
    assert validate_vectors([], 0, 2) == 2
    for vectors, count, dimension in (([[1.]], 2, None), ([[1.]], 1, 2),
                                      ([[float('nan')]], 1, None), ([[True]], 1, None), ([[]], 1, None)):
        with pytest.raises(ValueError):
            validate_vectors(vectors, count, dimension)
    result = subprocess.run([sys.executable, '-c',
        'import sys; from society0.kernel.vectors import validate_vectors; '
        'assert "society0.kernel.memory" not in sys.modules; '
        'assert "chromadb" not in sys.modules'], capture_output=True, text=True,
        env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'src')})
    assert result.returncode == 0, result.stderr
