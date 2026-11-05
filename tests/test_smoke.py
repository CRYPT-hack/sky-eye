import pytest

import engine


def test_version():
    assert engine.__version__ == "0.1.0"


def test_torch_imports():
    import torch

    assert torch.__version__


@pytest.mark.gpu
def test_cuda_available():
    import torch

    assert torch.cuda.is_available()
    assert "4050" in torch.cuda.get_device_name(0)
