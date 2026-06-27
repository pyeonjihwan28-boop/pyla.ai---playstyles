import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from lobby_automation import LobbyAutomation

class TestLobbyAutomation(unittest.TestCase):
    
    @patch("lobby_automation.load_toml_as_dict")
    def setUp(self, mock_load_toml):
        mock_load_toml.return_value = {
            'lobby': {
                'brawler_btn': (100, 100),
                'select_btn': (0, 0),
            }
        }
        self.mock_window_controller = MagicMock()
        self.mock_window_controller.width_ratio = 1
        self.mock_window_controller.height_ratio = 1
        self.lobby = LobbyAutomation(self.mock_window_controller)
    
    
    @patch("lobby_automation.time.sleep", return_value=None)
    @patch("lobby_automation.extract_text_and_positions")
    def test_can_select_brawlers(self, mock_extract_text, mock_sleep):
        """Tests that bot can select brawlers once he reaches the brawlers selection menu"""
        EXPECTED_BRAWLER_X = 2012
        EXPECTED_BRAWLER_Y = 978
        TOLERANCE = 50 
        
        mock_extract_text.return_value = {
            "shelly": {
                "center": (
                    EXPECTED_BRAWLER_X / 1.5385,
                    EXPECTED_BRAWLER_Y / 1.5385,
                )
            }
        }
        
        test_image = np.zeros((1080, 1920, 3), dtype=np.uint8)
        self.mock_window_controller.screenshot.return_value = test_image
        
        self.lobby.select_brawler("shelly")
        
        self.assertTrue(self.mock_window_controller.click.called, "No clicks were made at all")
        
        expected_scaled_x = EXPECTED_BRAWLER_X
        expected_scaled_y = EXPECTED_BRAWLER_Y
        
        self.assert_click_within_tolerance(expected_scaled_x, expected_scaled_y, TOLERANCE)

    def assert_click_within_tolerance(self, expected_x, expected_y, tolerance=50):
        """Helper method to check if any click was within tolerance of expected coordinates"""
        self.assertTrue(
            self.mock_window_controller.click.called,
            "No clicks were made"
        )
        
        click_calls = self.mock_window_controller.click.call_args_list
        
        for call in click_calls:
            actual_x, actual_y = call[0][0], call[0][1]
            distance_x = abs(actual_x - expected_x)
            distance_y = abs(actual_y - expected_y)
            
            if distance_x <= tolerance and distance_y <= tolerance:
                print(f"Click found at ({actual_x}, {actual_y}) within {tolerance}px of ({expected_x}, {expected_y})")
                return True
        
        click_coords = [(call[0][0], call[0][1]) for call in click_calls]
        self.fail(
            f"No click within {tolerance}px of ({expected_x}, {expected_y}). "
            f"Actual clicks: {click_coords}"
        )

if __name__ == "__main__":
    unittest.main()
