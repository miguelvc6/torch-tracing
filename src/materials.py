from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hittable import HitRecord
import torch as t
from jaxtyping import Bool, Float, jaxtyped
import torch.nn.functional as F
from typeguard import typechecked as typechecker

from utils import random_unit_vector


@jaxtyped(typechecker=typechecker)
def reflect(v: Float[t.Tensor, "N 3"], n: Float[t.Tensor, "N 3"]) -> Float[t.Tensor, "N 3"]:
    # Reflects vector v around normal n
    return v - 2 * (v * n).sum(dim=1, keepdim=True) * n


@jaxtyped(typechecker=typechecker)
def refract(
    uv: Float[t.Tensor, "N 3"], n: Float[t.Tensor, "N 3"], etai_over_etat: Float[t.Tensor, "N 1"]
) -> Float[t.Tensor, "N 3"]:
    # Computes the refracted vector
    cos_theta = t.minimum((-uv * n).sum(dim=1, keepdim=True), t.tensor(1.0, device=uv.device))
    r_out_perp = etai_over_etat * (uv + cos_theta * n)
    r_out_parallel = -t.sqrt(t.abs(1.0 - (r_out_perp**2).sum(dim=1, keepdim=True))) * n
    return r_out_perp + r_out_parallel


@jaxtyped(typechecker=typechecker)
def reflectance(cosine: Float[t.Tensor, "N 1"], ref_idx: Float[t.Tensor, "N 1"]) -> Float[t.Tensor, "N 1"]:
    # Schlick's approximation for reflectance
    r0 = ((1 - ref_idx) / (1 + ref_idx)) ** 2
    return r0 + (1 - r0) * (1 - cosine) ** 5


@jaxtyped(typechecker=typechecker)
class Material(ABC):
    @jaxtyped(typechecker=typechecker)
    def __init__(self):
        pass

    @abstractmethod
    @jaxtyped(typechecker=typechecker)
    def scatter(
        self,
        r_in: Float[t.Tensor, "* 3 2"],
        hit_record: "HitRecord",
    ) -> tuple[
        Bool[t.Tensor, "*"],
        Float[t.Tensor, "* 3"],
        Float[t.Tensor, "* 3 2"],
    ]:
        pass


@jaxtyped(typechecker=typechecker)
class Lambertian(Material):
    def __init__(self, albedo: Float[t.Tensor, "3"]):
        self.albedo = albedo

    @jaxtyped(typechecker=typechecker)
    def scatter(
        self,
        r_in: Float[t.Tensor, "N 3 2"],
        hit_record: "HitRecord",
    ) -> tuple[
        Bool[t.Tensor, "*"],
        Float[t.Tensor, "* 3"],
        Float[t.Tensor, "* 3 2"],
    ]:
        N = r_in.shape[0]
        normals = hit_record.normal
        points = hit_record.point

        # Generate scatter direction
        scatter_direction = normals + random_unit_vector((N, 3))

        # Handle degenerate scatter direction
        zero_mask = scatter_direction.norm(dim=1) < 1e-8
        scatter_direction[zero_mask] = normals[zero_mask]

        # Normalize scatter direction
        scatter_direction = F.normalize(scatter_direction, dim=-1)

        # Create new rays for recursion
        new_origin = points
        new_direction = scatter_direction
        new_rays = t.stack([new_origin, new_direction], dim=-1)

        # Attenuation is the albedo
        attenuation = self.albedo.expand(N, 3)

        scatter_mask = t.ones(N, dtype=t.bool, device=r_in.device)

        return scatter_mask, attenuation, new_rays


@jaxtyped(typechecker=typechecker)
class Metal(Material):
    def __init__(self, albedo: Float[t.Tensor, "3"], fuzz: float = 0.3):
        self.albedo = albedo
        self.fuzz = max(0.0, min(fuzz, 1.0))

    @jaxtyped(typechecker=typechecker)
    def scatter(
        self,
        r_in: Float[t.Tensor, "N 3 2"],
        hit_record: "HitRecord",
    ) -> tuple[
        Bool[t.Tensor, "*"],
        Float[t.Tensor, "N 3"],
        Float[t.Tensor, "N 3 2"],
    ]:
        N = r_in.shape[0]
        normals = hit_record.normal  # Shape: [N, 3]
        points = hit_record.point  # Shape: [N, 3]

        # Incoming ray directions
        in_directions = r_in[:, :, 1]  # Shape: [N, 3]
        in_directions = F.normalize(in_directions, dim=-1)

        # Generate reflected directions
        reflected_direction = reflect(in_directions, normals)

        reflected_direction = reflected_direction + self.fuzz * random_unit_vector((N, 3))
        reflected_direction = F.normalize(reflected_direction, dim=-1)

        # Check if reflected ray is above the surface
        dot_product = t.sum(reflected_direction * normals, dim=1)  # Shape: [N]
        scatter_mask = dot_product > 0  # Shape: [N], dtype: bool

        # Create new rays for recursion
        new_origin = points  # Shape: [N, 3]
        new_direction = reflected_direction  # Shape: [N, 3]
        new_rays = t.stack([new_origin, new_direction], dim=-1)  # Shape: [N, 3, 2]

        # Attenuation is the albedo
        attenuation = self.albedo.expand(N, 3)  # Shape: [N, 3]

        return scatter_mask, attenuation, new_rays


@jaxtyped(typechecker=typechecker)
class Dielectric(Material):
    def __init__(self, refraction_index: float):
        self.refraction_index = refraction_index

    @jaxtyped(typechecker=typechecker)
    def scatter(
        self,
        r_in: Float[t.Tensor, "N 3 2"],
        hit_record: "HitRecord",
    ) -> tuple[
        Bool[t.Tensor, "*"],
        Float[t.Tensor, "N 3"],
        Float[t.Tensor, "N 3 2"],
    ]:
        N = r_in.shape[0]
        normals = hit_record.normal  # Shape: [N, 3]
        points = hit_record.point  # Shape: [N, 3]
        front_face = hit_record.front_face  # Shape: [N], dtype: bool
        unit_direction = F.normalize(r_in[:, :, 1], dim=1)  # Shape: [N, 3]

        # Attenuation is always (1, 1, 1) for dielectric materials
        attenuation = t.ones(N, 3, device=r_in.device)  # Shape: [N, 3]

        # Compute the ratio of indices of refraction
        refraction_ratio = t.where(
            front_face.unsqueeze(1),
            t.full((N, 1), 1.0 / self.refraction_index, device=r_in.device),
            t.full((N, 1), self.refraction_index, device=r_in.device),
        )  # Shape: [N, 1]

        cos_theta = t.minimum(
            (-unit_direction * normals).sum(dim=1, keepdim=True), t.tensor(1.0, device=r_in.device)
        )  # Shape: [N, 1]
        sin_theta = t.sqrt(1.0 - cos_theta**2)  # Shape: [N, 1]

        # Determine if total internal reflection occurs
        cannot_refract = (refraction_ratio * sin_theta) > 1.0  # Shape: [N, 1], dtype: bool

        # Generate random numbers to decide between reflection and refraction
        reflect_prob = reflectance(cos_theta, refraction_ratio)  # Shape: [N, 1]
        random_numbers = t.rand(N, 1, device=r_in.device)
        should_reflect = cannot_refract | (reflect_prob > random_numbers)

        # Compute reflected and refracted directions
        reflected_direction = reflect(unit_direction, normals)  # Shape: [N, 3]
        refracted_direction = refract(unit_direction, normals, refraction_ratio)  # Shape: [N, 3]
        direction = t.where(should_reflect.expand(-1, 3), reflected_direction, refracted_direction)  # Shape: [N, 3]
        new_rays = t.stack([points, direction], dim=-1)  # Shape: [N, 3, 2]

        # Scatter mask is always True for dielectric materials
        scatter_mask = t.ones(N, dtype=t.bool, device=r_in.device)  # Shape: [N]

        return scatter_mask, attenuation, new_rays