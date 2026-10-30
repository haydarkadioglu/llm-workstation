from typing import Optional

from ..hub import drive


def _dtype():
    import torch

    return torch.float16 if torch.cuda.is_available() else torch.float32


def load_pipeline(model_id: str, kind: str, token: Optional[str]):
    import diffusers
    import torch

    common = dict(torch_dtype=_dtype(), token=token, cache_dir=drive.cache_dir())
    if kind == "video":
        pipeline = diffusers.DiffusionPipeline.from_pretrained(model_id, **common)
    else:
        errors = []
        for cls, extra in (
            (diffusers.AutoPipelineForText2Image, {"use_safetensors": True}),
            (diffusers.DiffusionPipeline, {}),
        ):
            try:
                pipeline = cls.from_pretrained(model_id, **common, **extra)
                break
            except Exception as err:
                errors.append(f"{cls.__name__}: {err}")
        else:
            raise ValueError("Failed to load image model. " + "; ".join(errors))

    if torch.cuda.is_available():
        pipeline.to("cuda")
    return pipeline


def generate_image(pipeline, prompt: str, negative_prompt: str, steps: int, guidance_scale: float, width: int, height: int):
    import torch

    with torch.inference_mode():
        return pipeline(
            prompt=prompt,
            negative_prompt=negative_prompt or None,
            num_inference_steps=steps,
            guidance_scale=guidance_scale,
            width=width,
            height=height,
        ).images[0]
