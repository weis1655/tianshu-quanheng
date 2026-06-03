#!/usr/bin/env python3
"""
Unit tests for BaseAgent class
"""

import unittest
import sys
import os
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, Mock

# Add the agents directory to the path
sys.path.insert(0, str(Path(__file__).parent.parent / "agents"))

from base_agent import BaseAgent, add_market_prefix, validate_and_prefix_codes


class ConcreteAgent(BaseAgent):
    """Concrete implementation of BaseAgent for testing"""

    def run(self, *args, **kwargs):
        return {"success": True}


class TestBaseAgent(unittest.TestCase):
    """Test cases for BaseAgent class"""

    def setUp(self):
        """Set up test fixtures"""
        self.agent = ConcreteAgent("TestAgent")
        self.temp_dir = Path(tempfile.mkdtemp())

    def tearDown(self):
        """Clean up temp files"""
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_initialization(self):
        """Test that agent initializes correctly"""
        self.assertEqual(self.agent.agent_name, "TestAgent")
        self.assertIsInstance(self.agent.root, Path)
        self.assertIn("llm_calls", self.agent.stats)
        self.assertEqual(self.agent.stats["llm_calls"], 0)

    def test_add_market_prefix_sh(self):
        """Test adding market prefix for SH stocks"""
        # Test 600xxx series
        self.assertEqual(add_market_prefix("600000"), "sh600000")
        self.assertEqual(add_market_prefix("601899"), "sh601899")
        # Test 500xxx series (also SH)
        self.assertEqual(add_market_prefix("500001"), "sh500001")
        # Test with existing prefix
        self.assertEqual(add_market_prefix("SH600000"), "sh600000")
        self.assertEqual(add_market_prefix("600000.SH"), "sh600000")

    def test_add_market_prefix_sz(self):
        """Test adding market prefix for SZ stocks"""
        # Test 000xxx series
        self.assertEqual(add_market_prefix("000001"), "sz000001")
        self.assertEqual(add_market_prefix("002001"), "sz002001")
        self.assertEqual(add_market_prefix("300001"), "sz300001")
        # Test with existing prefix
        self.assertEqual(add_market_prefix("SZ000001"), "sz000001")
        self.assertEqual(add_market_prefix("000001.SZ"), "sz000001")

    def test_add_market_prefix_invalid(self):
        """Test adding market prefix for invalid codes"""
        # Empty string
        self.assertEqual(add_market_prefix(""), "")
        # Too short
        self.assertEqual(add_market_prefix("12345"), "")
        # Too long
        self.assertEqual(add_market_prefix("1234567"), "")
        # Non-numeric
        self.assertEqual(add_market_prefix("abcdef"), "")

    def test_validate_and_prefix_codes(self):
        """Test validating and prefixing a list of codes"""
        codes = ["600000", "000001", "601899", "002001", "invalid", ""]
        result = validate_and_prefix_codes(codes)
        expected = ["sh600000", "sz000001", "sh601899", "sz002001"]
        self.assertEqual(result, expected)

    def test_safe_read_write_text(self):
        """Test safe text file operations"""
        test_file = self.temp_dir / "test_base_agent.txt"

        # Test writing
        result = self.agent.safe_write_text(test_file, "Hello, World!")
        self.assertTrue(result)
        self.assertTrue(test_file.exists())

        # Test reading
        content = self.agent.safe_read_text(test_file)
        self.assertEqual(content, "Hello, World!")

        # Test reading non-existent file
        content = self.agent.safe_read_text(self.temp_dir / "non_existent.txt", "default")
        self.assertEqual(content, "default")

    def test_safe_read_write_json(self):
        """Test safe JSON file operations"""
        test_file = self.temp_dir / "test_base_agent.json"
        test_data = {"name": "test", "value": 42, "list": [1, 2, 3]}

        # Test writing
        result = self.agent.safe_write_json(test_file, test_data)
        self.assertTrue(result)
        self.assertTrue(test_file.exists())

        # Test reading
        content = self.agent.safe_read_json(test_file)
        self.assertEqual(content, test_data)

        # Test reading non-existent file with default
        content = self.agent.safe_read_json(self.temp_dir / "non_existent.json", {"default": True})
        self.assertEqual(content, {"default": True})

    def test_stats_tracking(self):
        """Test that statistics are tracked correctly"""
        initial_stats = self.agent.get_stats()
        self.assertEqual(initial_stats["llm_calls"], 0)
        self.assertEqual(initial_stats["llm_errors"], 0)

        # Reset stats
        self.agent.reset_stats()
        reset_stats = self.agent.get_stats()
        self.assertEqual(reset_stats["llm_calls"], 0)
        self.assertEqual(reset_stats["llm_errors"], 0)
        self.assertIsNotNone(reset_stats["start_time"])

    def test_call_llm_mock(self):
        """Test mock LLM call"""
        mock_response = "This is a test response"

        with patch("requests.post") as mock_post:
            mock_post.return_value.json.return_value = {
                "choices": [{"message": {"content": mock_response}}]
            }
            mock_post.return_value.raise_for_status = Mock()

            result = self.agent.call_llm("test prompt", max_tokens=100)

            self.assertEqual(result, mock_response)
            self.assertEqual(self.agent.stats["llm_calls"], 1)


if __name__ == "__main__":
    unittest.main()