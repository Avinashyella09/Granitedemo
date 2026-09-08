import cv2
import numpy as np


class VolumeEstimator:
    """
    Estimates granite block dimensions, volume and tonnage
    using a segmented block mask and ArUco marker references.

    NOTE:
    This is a prototype geometry estimator.
    For production-grade measurement, camera calibration,
    perspective/pose estimation and robust 3D reconstruction
    should be added.
    """

    def __init__(
        self,
        primary_marker_id=1,
        side_marker_id=2,
        marker_size_cm=20.0
    ):
        """
        Args:
            primary_marker_id: ArUco marker ID on the front/primary face.
            side_marker_id: ArUco marker ID on the side face.
            marker_size_cm: Physical size of the ArUco marker in centimeters.
        """

        self.primary_marker_id = int(primary_marker_id)
        self.side_marker_id = int(side_marker_id)
        self.marker_size_cm = float(marker_size_cm)

        if self.marker_size_cm <= 0:
            raise ValueError("marker_size_cm must be greater than zero.")

    # ------------------------------------------------------------------
    # Helper: create physical coordinates for a square marker
    # ------------------------------------------------------------------
    def _marker_physical_corners(self):
        """
        Returns the physical coordinates of the marker corners.

        Order:
            top-left
            top-right
            bottom-right
            bottom-left
        """

        s = self.marker_size_cm / 2.0

        return np.array(
            [
                [-s,  s],
                [ s,  s],
                [ s, -s],
                [-s, -s]
            ],
            dtype=np.float32
        )

    # ------------------------------------------------------------------
    # Helper: calculate homography
    # ------------------------------------------------------------------
    def _calculate_homography(self, image_corners):
        """
        Calculates image -> physical-plane homography.
        """

        if image_corners is None:
            return None

        corners = np.asarray(image_corners, dtype=np.float32)

        if corners.shape != (4, 2):
            return None

        physical_corners = self._marker_physical_corners()

        H, status = cv2.findHomography(
            corners,
            physical_corners,
            method=0
        )

        if H is None:
            return None

        if not np.all(np.isfinite(H)):
            return None

        return H

    # ------------------------------------------------------------------
    # Helper: transform points using homography
    # ------------------------------------------------------------------
    def _transform_points(self, points, H):
        """
        Transforms 2D image points using a homography.
        """

        if H is None:
            return None

        points = np.asarray(points, dtype=np.float32)

        if points.ndim != 2 or points.shape[1] != 2:
            return None

        points_h = np.hstack(
            [
                points,
                np.ones((len(points), 1), dtype=np.float32)
            ]
        )

        transformed = (H @ points_h.T).T

        denominator = transformed[:, 2]

        valid = np.abs(denominator) > 1e-8

        if not np.any(valid):
            return None

        transformed = transformed[valid]

        transformed = (
            transformed[:, :2]
            / transformed[:, 2:3]
        )

        if not np.all(np.isfinite(transformed)):
            return None

        return transformed

    # ------------------------------------------------------------------
    # Helper: calculate planar bounding dimensions
    # ------------------------------------------------------------------
    def _calculate_planar_dimensions(self, points):
        """
        Calculates width and height from transformed planar points.
        """

        if points is None or len(points) < 4:
            return None

        min_x = float(np.min(points[:, 0]))
        max_x = float(np.max(points[:, 0]))

        min_y = float(np.min(points[:, 1]))
        max_y = float(np.max(points[:, 1]))

        width = max_x - min_x
        height = max_y - min_y

        if width <= 0 or height <= 0:
            return None

        return width, height

    # ------------------------------------------------------------------
    # Main estimation function
    # ------------------------------------------------------------------
    def estimate(
        self,
        mask,
        markers,
        density_mt_per_m3=2.7
    ):
        """
        Estimates granite block dimensions and volume.

        Args:
            mask:
                Binary segmentation mask.
                Expected dtype uint8 with values 0 or 255.

            markers:
                Dictionary returned by MarkerDetector.

            density_mt_per_m3:
                Granite density in metric tonnes per cubic metre.

        Returns:
            Dictionary containing measurement results.
        """

        # ==============================================================
        # 1. Validate density
        # ==============================================================

        try:
            density_mt_per_m3 = float(density_mt_per_m3)
        except (TypeError, ValueError):
            return {
                "status": "error",
                "error_message": "Invalid granite density.",
                "confidence": 0.0
            }

        if density_mt_per_m3 <= 0:
            return {
                "status": "error",
                "error_message": "Granite density must be greater than zero.",
                "confidence": 0.0
            }

        # ==============================================================
        # 2. Validate segmentation mask
        # ==============================================================

        if mask is None:
            return {
                "status": "error",
                "error_message": "No block segmentation mask provided.",
                "confidence": 0.0
            }

        mask = np.asarray(mask)

        if mask.ndim != 2:
            return {
                "status": "error",
                "error_message": "Segmentation mask must be a 2D image.",
                "confidence": 0.0
            }

        binary_mask = np.where(mask > 0, 255, 0).astype(np.uint8)

        if np.count_nonzero(binary_mask) == 0:
            return {
                "status": "error",
                "error_message": "Segmentation mask is empty.",
                "confidence": 0.0
            }

        # ==============================================================
        # 3. Validate markers
        # ==============================================================

        if markers is None or not isinstance(markers, dict):
            return {
                "status": "error",
                "error_message": "No marker detection data provided.",
                "confidence": 0.0
            }

        has_primary = self.primary_marker_id in markers
        has_side = self.side_marker_id in markers

        if not has_primary:
            return {
                "status": "error",
                "error_message": (
                    f"Primary marker "
                    f"(ID {self.primary_marker_id}) not detected."
                ),
                "confidence": 0.0
            }

        # ==============================================================
        # 4. Find block contour
        # ==============================================================

        contours, _ = cv2.findContours(
            binary_mask,
            cv2.RETR_EXTERNAL,
            cv2.CHAIN_APPROX_SIMPLE
        )

        if not contours:
            return {
                "status": "error",
                "error_message": (
                    "Failed to extract contours from block mask."
                ),
                "confidence": 0.0
            }

        # Largest contour = primary block candidate
        block_contour = max(
            contours,
            key=cv2.contourArea
        )

        contour_area = cv2.contourArea(block_contour)

        if contour_area <= 0:
            return {
                "status": "error",
                "error_message": "Block contour has zero area.",
                "confidence": 0.0
            }

        block_points = block_contour.reshape(-1, 2)

        if len(block_points) < 4:
            return {
                "status": "error",
                "error_message": "Not enough contour points for measurement.",
                "confidence": 0.0
            }

        # ==============================================================
        # 5. Primary marker
        # ==============================================================

        primary_marker = markers[self.primary_marker_id]

        if "corners" not in primary_marker:
            return {
                "status": "error",
                "error_message": "Primary marker corner data unavailable.",
                "confidence": 0.0
            }

        primary_corners = np.asarray(
            primary_marker["corners"],
            dtype=np.float32
        )

        if primary_corners.shape != (4, 2):
            return {
                "status": "error",
                "error_message": (
                    "Primary marker must contain four corner points."
                ),
                "confidence": 0.0
            }

        # ==============================================================
        # 6. Primary face homography
        # ==============================================================

        H_primary = self._calculate_homography(
            primary_corners
        )

        if H_primary is None:
            return {
                "status": "error",
                "error_message": (
                    "Unable to calculate primary-face homography."
                ),
                "confidence": 0.0
            }

        primary_points = self._transform_points(
            block_points,
            H_primary
        )

        if primary_points is None:
            return {
                "status": "error",
                "error_message": (
                    "Unable to transform block points "
                    "to the primary physical plane."
                ),
                "confidence": 0.0
            }

        primary_dimensions = self._calculate_planar_dimensions(
            primary_points
        )

        if primary_dimensions is None:
            return {
                "status": "error",
                "error_message": (
                    "Unable to calculate primary-face dimensions."
                ),
                "confidence": 0.0
            }

        length_cm, height_cm = primary_dimensions

        # ==============================================================
        # 7. Validate primary dimensions
        # ==============================================================

        if length_cm <= self.marker_size_cm:
            return {
                "status": "error",
                "error_message": (
                    "Estimated length is smaller than the physical "
                    "marker size. Check marker detection or segmentation."
                ),
                "confidence": 0.0
            }

        if height_cm <= self.marker_size_cm:
            return {
                "status": "error",
                "error_message": (
                    "Estimated height is smaller than the physical "
                    "marker size. Check marker detection or segmentation."
                ),
                "confidence": 0.0
            }

        # Convert cm -> m
        length_m = length_cm / 100.0
        height_m = height_cm / 100.0

        # ==============================================================
        # 8. Side marker / depth measurement
        # ==============================================================

        if not has_side:
            return {
                "status": "error",
                "error_message": (
                    "Single marker detected. Third-dimension "
                    "(depth/breadth) cannot be reliably reconstructed. "
                    f"Side marker ID {self.side_marker_id} is required."
                ),
                "confidence": 0.0
            }

        side_marker = markers[self.side_marker_id]

        if "corners" not in side_marker:
            return {
                "status": "error",
                "error_message": "Side marker corner data unavailable.",
                "confidence": 0.0
            }

        side_corners = np.asarray(
            side_marker["corners"],
            dtype=np.float32
        )

        if side_corners.shape != (4, 2):
            return {
                "status": "error",
                "error_message": (
                    "Side marker must contain four corner points."
                ),
                "confidence": 0.0
            }

        # ==============================================================
        # 9. Side face homography
        # ==============================================================

        H_side = self._calculate_homography(
            side_corners
        )

        if H_side is None:
            return {
                "status": "error",
                "error_message": (
                    "Unable to calculate side-face homography."
                ),
                "confidence": 0.0
            }

        side_points = self._transform_points(
            block_points,
            H_side
        )

        if side_points is None:
            return {
                "status": "error",
                "error_message": (
                    "Unable to transform block points "
                    "to the side physical plane."
                ),
                "confidence": 0.0
            }

        side_dimensions = self._calculate_planar_dimensions(
            side_points
        )

        if side_dimensions is None:
            return {
                "status": "error",
                "error_message": (
                    "Unable to calculate side-face dimensions."
                ),
                "confidence": 0.0
            }

        breadth_cm, _ = side_dimensions

        if breadth_cm <= self.marker_size_cm:
            return {
                "status": "error",
                "error_message": (
                    "Estimated breadth is smaller than the physical "
                    "marker size. Check side marker or segmentation."
                ),
                "confidence": 0.0
            }

        breadth_m = breadth_cm / 100.0

        # ==============================================================
        # 10. Calculate volume
        # ==============================================================

        volume_m3 = (
            length_m
            * breadth_m
            * height_m
        )

        if volume_m3 <= 0 or not np.isfinite(volume_m3):
            return {
                "status": "error",
                "error_message": "Invalid calculated volume.",
                "confidence": 0.0
            }

        # ==============================================================
        # 11. Calculate tonnage
        # ==============================================================

        tonnage_mt = (
            volume_m3
            * density_mt_per_m3
        )

        # ==============================================================
        # 12. Confidence
        # ==============================================================

        # Both required markers detected
        # and a valid segmentation contour exists.
        confidence = 0.95

        # ==============================================================
        # 13. Return result
        # ==============================================================

        return {
            "status": "success",

            "length_m": float(
                round(length_m, 3)
            ),

            "height_m": float(
                round(height_m, 3)
            ),

            "breadth_m": float(
                round(breadth_m, 3)
            ),

            "volume_m3": float(
                round(volume_m3, 4)
            ),

            "tonnage_mt": float(
                round(tonnage_mt, 3)
            ),

            "density_mt_per_m3": float(
                round(density_mt_per_m3, 3)
            ),

            "confidence": float(
                confidence
            )
        }