from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from math import ceil, floor
from typing import Any, Literal

from vsaa import Eedi3, Nnedi3, SuperSampler, masked_clamp_aa
from vsexprtools import aka_expr_available, expr_func
from vskernels import Catrom, Scaler, ScalerT, SetsuCubic
from vsrgtools import box_blur, gauss_blur, contrasharpening, contrasharpening_dehalo
from vstools import (
    Matrix, MatrixT, PlanesT, Transfer, VSFunction, check_ref_clip, check_variable, core, depth,
    fallback, get_depth, get_w, inject_self, vs, expect_bits, padder, DependencyNotFoundError
)

from .gamma import gamma2linear, linear2gamma
from .helpers import GenericScaler

__all__ = [
    'DPID',
    'SSIM', 'ssim_downsample',
    'DLISR',
    'Waifu2x'
]


@dataclass
class DPID(GenericScaler):
    """@@PLACEHOLDER@@"""

    sigma: float = 0.1
    """@@PLACEHOLDER@@"""

    ref: vs.VideoNode | ScalerT | None = None
    """@@PLACEHOLDER@@"""

    planes: PlanesT = None
    """@@PLACEHOLDER@@"""

    @inject_self
    def scale(  # type: ignore[override]
        self, clip: vs.VideoNode, width: int, height: int, shift: tuple[float, float] = (0, 0), **kwargs: Any
    ) -> vs.VideoNode:
        ref = clip

        if isinstance(self.ref, vs.VideoNode):
            check_ref_clip(clip, self.ref)  # type: ignore
            ref = self.ref  # type: ignore

        scaler = Scaler.ensure_obj(self.ref if isinstance(self.ref, Scaler) else self.scaler, self.__class__)

        if (ref.width, ref.height) != (width, height):
            ref = scaler.scale(ref, width, height)

        kwargs |= {
            'lambda_': self.sigma, 'planes': self.planes,
            'src_left': shift[1], 'src_top': shift[0]
        } | kwargs | {'read_chromaloc': True}

        return core.dpid.DpidRaw(clip, ref, **kwargs)


@dataclass
class SSIM(GenericScaler):
    """
    SSIM downsampler is an image downscaling technique that aims to optimize
    for the perceptual quality of the downscaled results.

    Image downscaling is considered as an optimization problem
    where the difference between the input and output images is measured
    using famous Structural SIMilarity (SSIM) index.

    The solution is derived in closed-form, which leads to the simple, efficient implementation.
    The downscaled images retain perceptually important features and details,
    resulting in an accurate and spatio-temporally consistent representation of the high resolution input.
    """

    smooth: int | float | VSFunction | None = None
    """
    Image smoothening method.
    If you pass an int, it specifies the "radius" of the internally-used boxfilter,
    i.e. the window has a size of (2*smooth+1)x(2*smooth+1).
    If you pass a float, it specifies the "sigma" of gauss_blur,
    i.e. the standard deviation of gaussian blur.
    If you pass a function, it acts as a general smoother.
    Default uses a gaussian blur.
    """

    curve: Transfer | bool | None = None
    """
    Perform a gamma conversion prior to scaling and after scaling. This must be set for `sigmoid` to function.
    If True it will auto-determine the curve based on the input props or resolution.
    Can be specified with for example `curve=TransferCurve.BT709`.
    """

    sigmoid: bool | None = None
    """When True, applies a sigmoidal curve after the power-like curve
    (or before when converting from linear to gamma-corrected).
    This helps reduce the dark halo artefacts found around sharp edges
    caused by resizing in linear luminance.
    This parameter only works if `gamma=True`.
    """

    epsilon: float = 1e-6
    """Variable used for math operations."""

    @inject_self
    def scale(  # type: ignore[override]
        self, clip: vs.VideoNode, width: int, height: int, shift: tuple[float, float] = (0, 0),
        smooth: int | float | VSFunction = ((3 ** 2 - 1) / 12) ** 0.5,
        curve: Transfer | bool = False, sigmoid: bool = False
    ) -> vs.VideoNode:
        assert check_variable(clip, self.scale)

        smooth = fallback(self.smooth, smooth)  # type: ignore
        curve = fallback(self.curve, curve)  # type: ignore
        sigmoid = fallback(self.sigmoid, sigmoid)

        if callable(smooth):
            filter_func = smooth
        elif isinstance(smooth, int):
            filter_func = partial(box_blur, radius=smooth)
        elif isinstance(smooth, float):
            filter_func = partial(gauss_blur, sigma=smooth)

        if curve is True:
            try:
                curve = Transfer.from_video(clip, True)
            except ValueError:
                curve = Transfer.from_matrix(Matrix.from_video(clip))

        bits, clip = get_depth(clip), depth(clip, 32)

        if curve:
            clip = gamma2linear(clip, curve, sigmoid=sigmoid, epsilon=self.epsilon)

        l1 = self._scaler.scale(clip, width, height, shift)

        l1_sq, c_sq = [expr_func(x, 'x dup *') for x in (l1, clip)]

        l2 = self._scaler.scale(c_sq, width, height, shift)

        m, sl_m_square, sh_m_square = [filter_func(x) for x in (l1, l1_sq, l2)]

        if aka_expr_available:
            merge_expr = f'z dup * SQ! x SQ@ - SQD! SQD@ {self.epsilon} < 0 y SQ@ - SQD@ / sqrt ?'
        else:
            merge_expr = f'x z dup * - {self.epsilon} < 0 y z dup * - x z dup * - / sqrt ?'

        r = expr_func([sl_m_square, sh_m_square, m], merge_expr)
        t = expr_func([r, m], 'x y *')
        d = expr_func([filter_func(m), filter_func(r), l1, filter_func(t)], 'x y z * + a -')

        if curve:
            d = linear2gamma(d, curve, sigmoid=sigmoid)

        return depth(d, bits)


def ssim_downsample(
    clip: vs.VideoNode, width: int | None = None, height: int = 720,
    smooth: int | float | VSFunction = ((3 ** 2 - 1) / 12) ** 0.5,
    scaler: ScalerT = Catrom,
    curve: Transfer | bool = False, sigmoid: bool = False,
    shift: tuple[float, float] = (0, 0), epsilon: float = 1e-6
) -> vs.VideoNode:
    import warnings
    warnings.warn("ssim_downsample is deprecated! You should use SSIM directly!", DeprecationWarning)
    return SSIM(epsilon=epsilon, scaler=scaler).scale(
        clip, fallback(width, get_w(height, clip)), height, shift, smooth, curve, sigmoid
    )


@dataclass
class DLISR(GenericScaler):
    """@@PLACEHOLDER@@"""

    scaler: ScalerT = DPID(0.5, SetsuCubic)
    """@@PLACEHOLDER@@"""

    matrix: MatrixT | None = None
    """@@PLACEHOLDER@@"""

    device_id: int | None = None
    """@@PLACEHOLDER@@"""

    @inject_self
    def scale(  # type: ignore
        self, clip: vs.VideoNode, width: int, height: int, shift: tuple[float, float] = (0, 0),
        *, matrix: MatrixT | None = None, **kwargs: Any
    ) -> vs.VideoNode:
        output = clip

        assert check_variable(clip, self.__class__)

        if width > clip.width or height > clip.width:
            if not matrix:
                matrix = Matrix.from_param(matrix or self.matrix, self.__class__) or Matrix.from_video(clip, False)

            output = self._kernel.resample(output, vs.RGBS, Matrix.RGB, matrix)
            output = output.std.Limiter()

            max_scale = max(ceil(width / clip.width), ceil(height / clip.height))

            output = output.akarin.DLISR(max_scale, self.device_id)

        return self._finish_scale(output, clip, width, height, shift, matrix)


@dataclass
class Waifu2x(GenericScaler):
    cuda: bool | Literal['trt'] = True
    opencl: bool = True
    clamper: type[SuperSampler] | SuperSampler | Literal[False] = Nnedi3
    aa: VSFunction | bool = False
    num_streams: int = 1
    fp16: bool = True
    matrix: MatrixT | None = None

    @classmethod
    def mod_padding(cls, clip: vs.VideoNode, mod: int = 4, min: int = 4) -> tuple[int, int, int, int]:
        ph, pv = (mod - (((x + min * 2) - 1) % mod + 1) for x in (clip.width, clip.height))
        left, top = floor(ph / 2), floor(pv / 2)
        return tuple(x + min for x in (left, ph - left, top, pv - top))  # type: ignore

    def __post_init__(self) -> None:
        try:
            from vsmlrt import Backend  # type: ignore
        except ModuleNotFoundError as e:
            raise DependencyNotFoundError(self.__class__, e)

        if self.cuda is True:
            self.backend = Backend.ORT_CUDA(num_streams=self.num_streams, fp16=self.fp16)
        elif self.cuda is False:
            self.backend = Backend.NCNN_VK(num_streams=self.num_streams, fp16=self.fp16)
        else:
            self.backend = Backend.TRT(num_streams=self.num_streams, fp16=self.fp16)

        if self.scaler is None:
            self.scaler = SSIM

        if self.clamper and hasattr(self.clamper, 'opencl'):
            self.clamper = self.clamper.copy(opencl=self.opencl)  # type: ignore

        self._clamper = SuperSampler.ensure_obj(self.clamper, self.__class__) if self.clamper else None

        super().__post_init__()

    @inject_self
    def scale(  # type:ignore
        self, clip: vs.VideoNode, width: int, height: int, shift: tuple[float, float] = (0, 0),
        *, matrix: MatrixT | None = None, **kwargs: Any
    ) -> vs.VideoNode:
        output = clip

        assert check_variable(clip, self.scale)

        if width > clip.width or height > clip.width:
            from vsmlrt import Waifu2x

            if clip.format.color_family is vs.YUV:
                if not matrix:
                    matrix = Matrix.from_param(matrix or self.matrix, self.__class__) or Matrix.from_video(clip, False)
                wclip = self._kernel.resample(output, vs.RGBS, Matrix.RGB, matrix)
            else:
                wclip = output

            padding = self.mod_padding(wclip)
            clip32, bits = expect_bits(wclip, 32)
            dsrgb = padder(clip32.std.Limiter(), *padding)

            if clip.format.color_family is vs.GRAY:
                dsrgb = dsrgb.std.ShufflePlanes(0, vs.RGB)

            up = Waifu2x(dsrgb, noise=-1, model=6, backend=self.backend, **kwargs)

            if clip.format.color_family is vs.GRAY:
                up = up.std.ShufflePlanes(0, vs.GRAY)

            up = up.std.Crop(*(p * 2 for p in padding)).std.Expr('x 0.5 255 / +')

            output = depth(up, bits).std.CopyFrameProps(clip32)

            if self._clamper:
                ss = self._clamper.scale(wclip, output.width, output.height)
                output = contrasharpening(output.std.Merge(ss, 3 / 4), ss)

            if self.aa:
                if self.aa is True:
                    from vsdehalo import fine_dehalo

                    eedi3 = Eedi3(0.85, 0.15, 400, 3, 10, vcheck=3, opencl=self.opencl)
                    caa = masked_clamp_aa(output, strength=8.5, strong_aa=eedi3, opencl=self.opencl)

                    fdh = fine_dehalo(
                        caa, rx=2.4, ry=2.4, brightstr=0.95, darkstr=0.25, lowsens=30,
                        highsens=80, thma=200, thmi=20, ss=1
                    )
                    output = contrasharpening_dehalo(fdh, output, level=1.0, alpha=1.5)
                elif callable(self.aa):
                    output = self.aa(output)

        return self._finish_scale(output, wclip, width, height, shift)
