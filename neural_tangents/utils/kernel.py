# Copyright 2019 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""The `Kernel` class containing NTK and NNGP `np.ndarray`s as fields."""

import collections
import enum
from neural_tangents.utils import utils
import jax.numpy as np


class Marginalisation(enum.IntEnum):
  """Types of marginal distributions for which covariances can be computed.

  Let k_{ij}(x, y) represent a covariance between the spatial dimensions i and j
  for the inputs x and y (for multiple spatial dimensions, imagine i and j have
  multiple entries). Then and instance of `Kernel` with `marginal`/`cross`:

  `OVER_ALL`: (used for architectures with no spatial dimensions)
    `var1`/`var2`: k(x, x), shape is `(batch_size,)`
    `nnpg`/`ntk`: k(x, y), shape is `(batch_size_1, batch_size_2)`
  `OVER_PIXELS`:
    `var1`/`var2`: k_{ii}(x, x), shape is
     `(batch_size, spatial_dim_1, spatial_dim_2, ...)`
    `nngp`/`ntk`: k_{ii}(x, y), shape is
     `(batch_size_1, batch_size_2, spatial_dim_1, spatial_dim_2, ...)`
  `OVER_POINTS`:
    `var1`/`var2`: k_{ij}(x, x), shape is
     `(batch_size, spatial_dim_1, spatial_dim_1, spatial_dim_2, spatial_dim_2,
       ...)`
    `nngp`/`ntk`: not allowed; please use `NO` instead
  `NO`:
    `var1`/`var2`: k_{ij}(x, y), shape is
     `(batch_size, batch_size,
      spatial_dim_1, spatial_dim_1, spatial_dim_2, spatial_dim_2, ...)`
    `nngp`/`ntk`: k_{ij}(x, y)
     `(batch_size_1, batch_size_2,
      spatial_dim_1, spatial_dim_1, spatial_dim_2, spatial_dim_2, ...)`

  The number associated with each instance of this enum represents the relative
  amount of information being tracked compared to the other options, i.e.,
  the information tracked by `OVER_PIXELS` is a strict subset of that tracked
  by `OVER_POINTS`, which itself tracks a strict subset of information
  compared to the `NO` option. Note that this is an `IntEnum`
  meaning that comparison operators `<`, `==` work as set inclusion and equality
  (and `>`, `<=`, `>=` as would be expected given this definition).
  """
  OVER_ALL = 0
  OVER_PIXELS_AND_POINTS = 1
  OVER_PIXELS = 2
  OVER_POINTS = 3
  NO = 4


class Kernel(
    collections.namedtuple('Kernel', [
        'var1', 'nngp', 'var2', 'ntk', 'is_gaussian', 'is_reversed',
        'marginal', 'cross', 'shape1', 'shape2', 'x1_is_x2', 'is_input',
        'mask1', 'mask2'
    ])):
  """A tuple containing information about the analytic NTK and NNGP of a model.

  Attributes:
    var1: variances of the first batch of inputs. A `np.ndarray` with shape
      `[batch_size_1]` for fully-connected networks, or matching the one
      specified by the `marginal` argument for CNNs with `batch_size_1` for data
      dimension(s).
    nngp: covariance between the first and second input (NNGP). A `np.ndarray`
      of shape `[batch_size_1, batch_size_2]` for fully-connected networks, or
      matching the one specifed by the `cross` argument for CNNs with
      `[batch_size_1, batch_size_2]` for data dimensions.
    var2: optional variances of the second batch of inputs. A `np.ndarray` with
      shape `[batch_size_2]` for fully-connected networks or matching the one
      specified by the `marginal` argument for CNNs with `batch_size_2` for data
      dimension(s).
    ntk: the neural tangent kernel (NTK). `np.ndarray` of same shape as `nngp`.
    is_gaussian: a boolean, specifying whether the output features or channels
      of the layer / NN function (returning this `Kernel` as the `kernel_fn`)
      are i.i.d. Gaussian with covariance `nngp`, conditioned on fixed inputs to
      the layer and i.i.d. Gaussian weights and biases of the layer. For
      example, passing an input through a CNN layer with i.i.d. Gaussian weights
      and biases produces i.i.d. Gaussian random variables along the channel
      dimension, while passing an input through a nonlinearity does not.
    is_reversed: a boolean specifying whether the covariance matrices `nngp`,
      `var1`, `var2`, and `ntk` have spatial dimensions reversed. Set to `False`
      and ignored unless `marginal` and `cross` are at least
      `Marginalisation.OVER_POINTS` and `Marginalisation.NO` respectively.
      Used internally to avoid self-cancelling transpositions in a sequence of
      CNN layers that flip the order of kernel spatial dimensions.
    marginal: an instance of `Marginalisation` enum or its ID, specifying types
      of covariances between spatial dimensions tracked in `var1`/`var2`.
    cross: an instance of `Marginalisation` enum or its ID, specifying types of
      covariances between spatial dimensions tracked in `nngp`/`ntk`.
    shape1: a tuple specifying the shape of the random variable in the first
      batch of inputs. These have variance `var1` and covariance with the second
      batch of inputs given by `nngp`.
    shape2: a tuple specifying the shape of the random variable in the second
      batch of inputs. These have variance `var2` and covariance with the first
      batch of inputs given by `nngp`.
    x1_is_x2: a boolean specifying whether `x1` and `x2` are the same.
    is_input: a boolean specifying whether the current layer is the input
        layer and it is used to avoid applying dropout to the input layer.
    mask1: An optional boolean `np.ndarray` with a shape broadcastable to
      `shape1` (and the same number of dimensions). `True` stands for the
      input being masked at that position, while `False` means the input is
      visible. For example, if `shape1 == (5, 32, 32, 3)` (a batch of 5 NHWC
      CIFAR10 images), a `mask1` of shape `(5, 1, 32, 1)` means different
      images can have different blocked columns (`H` and `C` dimensions are
      always either both blocked or unblocked). `None` means no masking.
    mask2: same as `mask1`, but for the second batch of inputs.
  """

  def __new__(cls, var1, nngp, var2, ntk, is_gaussian, is_reversed, marginal,
              cross, shape1, shape2, x1_is_x2, is_input, mask1, mask2):
    """Returns a `Kernel`.

    Args:
      var1: variances of the first batch of inputs. A `np.ndarray` with shape
        `[batch_size_1]` for fully-connected networks, or matching the one
        specified by the `marginal` argument for CNNs with `batch_size_1` for
        data dimension(s).
      nngp: covariance between the first and second input (NNGP). A `np.ndarray`
        of shape `[batch_size_1, batch_size_2]` for fully-connected networks, or
        matching the one specifed by the `cross` argument for CNNs with
        `[batch_size_1, batch_size_2]` for data dimensions.
      var2: optional variances of the second batch of inputs. A `np.ndarray`
        with shape `[batch_size_2]` for fully-connected networks or matching the
        one  specified by the `marginal` argument for CNNs with `batch_size_2`
        for data dimension(s).
      ntk: the neural tangent kernel (NTK). `np.ndarray` of same shape as
        `nngp`.
      is_gaussian: a boolean, specifying whether the output features or channels
        of the layer / NN function (returning this `Kernel` as the `kernel_fn`)
        are i.i.d. Gaussian with covariance `nngp`, conditioned on fixed inputs
        to the layer and i.i.d. Gaussian weights and biases of the layer. For
        example, passing an input through a CNN layer with i.i.d. Gaussian
        weights and biases produces i.i.d. Gaussian random variables along the
        channel dimension, while passing an input through a nonlinearity does
        not.
      is_reversed: a boolean specifying whether the covariance matrices
        `nngp`, `var1`, `var2`, and `ntk` have spatial dimensions reversed.
        Set to `False` and ignored unless `marginal` and `cross` are at least
        `Marginalisation.OVER_POINTS` and `Marginalisation.NO` respectively.
        Used internally to avoid self-cancelling transpositions in a sequence of
        CNN layers that flip the order of kernel spatial dimensions.
      marginal: an instance of `Marginalisation` enum or its ID, specifying
        types of covariances between spatial dimensions tracked in
        `var1`/`var2`.
      cross: an instance of `Marginalisation` enum or its ID, specifying types
        of covariances between spatial dimensions tracked in `nngp`/`ntk`.
      shape1: a tuple specifying the shape of the random variable in the first
        batch of inputs. These have variance `var1` and covariance with the
        second batch of inputs given by `nngp`.
      shape2: a tuple specifying the shape of the random variable in the second
        batch of inputs. These have variance `var2` and covariance with the
        first batch of inputs given by `nngp`.
      x1_is_x2: a boolean specifying whether `x1` and `x2` are the same.
      is_input: a boolean specifying whether the current layer is the input
        layer and it is used to avoid applying dropout to the input layer.
      mask1: An optional boolean `np.ndarray` with a shape broadcastable to
        `shape1` (and the same number of dimensions). `True` stands for the
        input being masked at that position, while `False` means the input is
        visible. For example, if `shape1 == (5, 32, 32, 3)` (a batch of 5 NHWC
        CIFAR10 images), a `mask1` of shape `(5, 1, 32, 1)` means different
        images can have different blocked columns (`H` and `C` dimensions are
        always either both blocked or unblocked). `None` means no masking.
      mask2: same as `mask1`, but for the second batch of inputs.
    Returns:
      A `Kernel`.
    """
    if isinstance(marginal, Marginalisation):
      marginal = int(marginal)
    if isinstance(cross, Marginalisation):
      cross = int(cross)
    return super(Kernel, cls).__new__(
        cls, var1, nngp, var2, ntk, is_gaussian, is_reversed, marginal, cross,
        shape1, shape2, x1_is_x2, is_input, mask1, mask2)

  def _replace(self, **kwargs):
    """`namedtuple._replace` with casting `Marginalisation` to `int`s."""
    for k in kwargs:
      if isinstance(kwargs[k], Marginalisation):
        kwargs[k] = int(kwargs[k])
    return super(Kernel, self)._replace(**kwargs)

  def reverse(self):
    """Reverse the order of spatial axes in the covariance matrices.

    Args:
      self: a `Kernel` object.

    Returns:
      A `Kernel` object with spatial axes order flipped in
      all covariance matrices. For example, if `kernels.nngp` has shape
      `[batch_size_1, batch_size_2, H, H, W, W, D, D, ...]`, then
      `reverse(kernels).nngp` has shape
      `[batch_size_1, batch_size_2, ..., D, D, W, W, H, H]`.
    """
    var1, nngp, var2, ntk, shape1 = (self.var1, self.nngp, self.var2,
                                     self.ntk, self.shape1)

    def reverse(mat):
      return utils.revert_zipped(mat, self.shape1)

    var1, nngp, var2, ntk = map(reverse, (var1, nngp, var2, ntk))

    return self._replace(var1=var1, nngp=nngp, var2=var2, ntk=ntk,
                         is_reversed=not self.is_reversed)
