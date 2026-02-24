import cv2

class VideoCapture:
    def __init__(self, camera_index=0):
        self.camera_index = camera_index
        self.cap = None

    def start(self):
        """Initializes the video capture device."""
        self.cap = cv2.VideoCapture(self.camera_index)
        if not self.cap.isOpened():
            print(f"Error: Could not open video device {self.camera_index}.")
            return False
        return True

    def get_frame(self):
        """Reads a single frame from the camera."""
        if self.cap is None or not self.cap.isOpened():
            return None
        ret, frame = self.cap.read()
        if ret:
            return frame
        return None

    def stop(self):
        """Releases the camera resources."""
        if self.cap:
            self.cap.release()
            self.cap = None

if __name__ == "__main__":
    # Simple test
    vc = VideoCapture()
    if vc.start():
        frame = vc.get_frame()
        if frame is not None:
            print(f"Captured frame of shape: {frame.shape}. Previewing is disabled for headless servers.")
        vc.stop()
