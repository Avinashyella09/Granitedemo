import cv2
import numpy as np


class MarkerDetector:
    """
    Detects ArUco markers and calculates their geometric properties.

    The markers act as known physical references for measuring the
    granite block.

    Default:
        ArUco dictionary : DICT_5X5_50
        Marker size      : 20 cm
    """

    def __init__(self, marker_size_cm=20.0):
        self.marker_size_cm = float(marker_size_cm)

        # ---------------------------------------------------------
        # ArUco dictionary
        # ---------------------------------------------------------
        self.aruco_dict = cv2.aruco.getPredefinedDictionary(
            cv2.aruco.DICT_5X5_50
        )

        # ---------------------------------------------------------
        # Detector parameters
        # ---------------------------------------------------------
        try:
            # OpenCV newer API
            self.aruco_params = cv2.aruco.DetectorParameters()
            self.aruco_detector = cv2.aruco.ArucoDetector(
                self.aruco_dict,
                self.aruco_params
            )

            self.new_api = True

        except AttributeError:
            # OpenCV older API
            self.aruco_params = cv2.aruco.DetectorParameters_create()
            self.aruco_detector = None

            self.new_api = False

    # =============================================================
    # MARKER DETECTION
    # =============================================================

    def _detect_markers(self, gray):
        """
        Detect ArUco markers while supporting different OpenCV versions.
        """

        if self.new_api:
            corners, ids, rejected = self.aruco_detector.detectMarkers(gray)
        else:
            corners, ids, rejected = cv2.aruco.detectMarkers(
                gray,
                self.aruco_dict,
                parameters=self.aruco_params
            )

        return corners, ids, rejected

    # =============================================================
    # DEFAULT CAMERA MATRIX
    # =============================================================

    @staticmethod
    def get_default_camera_matrix(image_shape):
        """
        Creates a rough camera matrix when actual camera calibration
        parameters are not available.

        IMPORTANT:
        This is only a fallback.
        For accurate field measurements we should eventually use
        a real calibrated camera.
        """

        h, w = image_shape[:2]

        focal_length = max(w, h)

        camera_matrix = np.array(
            [
                [focal_length, 0, w / 2.0],
                [0, focal_length, h / 2.0],
                [0, 0, 1]
            ],
            dtype=np.float32
        )

        dist_coeffs = np.zeros((5, 1), dtype=np.float32)

        return camera_matrix, dist_coeffs

    # =============================================================
    # MARKER POSE
    # =============================================================

    def _estimate_pose(
        self,
        corners,
        camera_matrix,
        dist_coeffs
    ):
        """
        Estimates marker rotation and translation using solvePnP.

        Marker coordinate system:

              (-s/2,+s/2) -------- (+s/2,+s/2)
                    |                  |
                    |      marker      |
                    |                  |
              (-s/2,-s/2) -------- (+s/2,-s/2)

        where s = marker size in cm.
        """

        s = self.marker_size_cm

        object_points = np.array(
            [
                [-s / 2,  s / 2, 0],
                [ s / 2,  s / 2, 0],
                [ s / 2, -s / 2, 0],
                [-s / 2, -s / 2, 0]
            ],
            dtype=np.float32
        )

        image_points = corners.astype(np.float32)

        success, rvec, tvec = cv2.solvePnP(
            object_points,
            image_points,
            camera_matrix,
            dist_coeffs,
            flags=cv2.SOLVEPNP_ITERATIVE
        )

        if not success:
            return None, None

        return rvec, tvec

    # =============================================================
    # PIXEL SCALE
    # =============================================================

    def _calculate_pixel_scale(self, corners):
        """
        Calculates approximate pixels-per-centimeter using the
        four sides of the ArUco marker.

        This is useful as a quick scale reference.

        It is NOT the final 3D measurement method.
        """

        side_lengths = [
            np.linalg.norm(corners[0] - corners[1]),
            np.linalg.norm(corners[1] - corners[2]),
            np.linalg.norm(corners[2] - corners[3]),
            np.linalg.norm(corners[3] - corners[0])
        ]

        mean_side_px = float(np.mean(side_lengths))

        if self.marker_size_cm <= 0:
            return None

        pixel_scale = mean_side_px / self.marker_size_cm

        return float(pixel_scale)

    # =============================================================
    # MAIN DETECTION FUNCTION
    # =============================================================

    def detect(
        self,
        image,
        camera_matrix=None,
        dist_coeffs=None
    ):
        """
        Detect all ArUco markers in an image.

        Parameters
        ----------
        image : numpy.ndarray
            Input BGR image.

        camera_matrix : numpy.ndarray, optional
            Camera intrinsic matrix.

        dist_coeffs : numpy.ndarray, optional
            Camera distortion coefficients.

        Returns
        -------
        dict

        Example:

        {
            1: {
                "corners": ...,
                "center": ...,
                "rvec": ...,
                "tvec": ...,
                "pixel_scale": ...,
                "marker_size_cm": 20.0
            }
        }
        """

        if image is None:
            return {}

        if not isinstance(image, np.ndarray):
            raise TypeError(
                "image must be a NumPy array."
            )

        if image.ndim == 2:
            gray = image

        elif image.ndim == 3:
            gray = cv2.cvtColor(
                image,
                cv2.COLOR_BGR2GRAY
            )

        else:
            raise ValueError(
                "Unsupported image shape."
            )

        # ---------------------------------------------------------
        # Detect markers
        # ---------------------------------------------------------

        corners, ids, rejected = self._detect_markers(gray)

        results = {}

        if ids is None or len(ids) == 0:
            return results

        ids = ids.flatten()

        # ---------------------------------------------------------
        # Camera parameters
        # ---------------------------------------------------------

        if camera_matrix is None:
            camera_matrix, dist_coeffs = (
                self.get_default_camera_matrix(image.shape)
            )

        elif dist_coeffs is None:
            dist_coeffs = np.zeros(
                (5, 1),
                dtype=np.float32
            )

        # ---------------------------------------------------------
        # Process every detected marker
        # ---------------------------------------------------------

        for i, marker_id in enumerate(ids):

            marker_corners = corners[i][0].astype(
                np.float32
            )

            # Center
            center = np.mean(
                marker_corners,
                axis=0
            )

            # Pixel scale
            pixel_scale = self._calculate_pixel_scale(
                marker_corners
            )

            # Pose
            rvec, tvec = self._estimate_pose(
                marker_corners,
                camera_matrix,
                dist_coeffs
            )

            # -----------------------------------------------------
            # Store marker information
            # -----------------------------------------------------

            results[int(marker_id)] = {

                "corners": marker_corners,

                "center": center,

                "rvec": rvec,

                "tvec": tvec,

                "pixel_scale": pixel_scale,

                "marker_size_cm": self.marker_size_cm
            }

        return results