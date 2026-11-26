"""Hermetic tests for the saved YouTube cookie state (no network access)."""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from mobile.backend.music_service import (
    SIGNIN_BROKEN_MESSAGE,
    SIGNIN_CONFIGURED,
    SIGNIN_EXPIRED,
    SIGNIN_INVALID,
    SIGNIN_MISSING,
    SIGNIN_NO_AUTH,
    SIGNIN_NOT_CONFIGURED,
    refusal_is_signin,
    youtube_signin_state,
)

REFUSAL = (
    "YouTube could not provide this audio. It may be unavailable, "
    "region/age restricted, or require sign-in. Try another recording."
)


def cookie_line(name, *, domain=".youtube.com", expiry=0, value="value"):
    return f"{domain}\tTRUE\t/\tFALSE\t{expiry}\t{name}\t{value}\n"


class SignInStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def write(self, text, name="cookies.txt"):
        path = self.directory / name
        path.write_text(text)
        return path

    def test_unset_variable_is_not_configured(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(youtube_signin_state(), SIGNIN_NOT_CONFIGURED)
        self.assertEqual(youtube_signin_state("   "), SIGNIN_NOT_CONFIGURED)

    def test_a_configured_path_that_does_not_exist_is_missing(self):
        self.assertEqual(
            youtube_signin_state(str(self.directory / "absent.txt")),
            SIGNIN_MISSING,
        )

    def test_a_zero_byte_file_is_invalid(self):
        self.assertEqual(youtube_signin_state(str(self.write(""))), SIGNIN_INVALID)

    def test_preferences_without_auth_cookies_are_not_a_signin(self):
        self.assertEqual(
            youtube_signin_state(str(self.write(cookie_line("PREF")))),
            SIGNIN_NO_AUTH,
        )

    def test_auth_cookies_are_configured(self):
        self.assertEqual(
            youtube_signin_state(str(self.write(cookie_line("SAPISID")))),
            SIGNIN_CONFIGURED,
        )
        future = int(time.time()) + 3600
        path = self.write(cookie_line("SID", expiry=future), "future.txt")
        self.assertEqual(youtube_signin_state(str(path)), SIGNIN_CONFIGURED)

    def test_only_expired_auth_cookies_are_expired(self):
        past = int(time.time()) - 3600
        path = self.write(cookie_line("SAPISID", expiry=past))
        self.assertEqual(youtube_signin_state(str(path)), SIGNIN_EXPIRED)
        # One live auth cookie keeps the whole export usable.
        mixed = self.write(
            cookie_line("SAPISID", expiry=past) + cookie_line("SID", expiry=0),
            "mixed.txt",
        )
        self.assertEqual(youtube_signin_state(str(mixed)), SIGNIN_CONFIGURED)

    def test_env_variable_is_the_default_source(self):
        path = self.write(cookie_line("SAPISID"))
        with patch.dict(os.environ, {"EMBER_YOUTUBE_COOKIES": str(path)}):
            self.assertEqual(youtube_signin_state(), SIGNIN_CONFIGURED)

    def test_the_broken_message_names_the_action(self):
        self.assertIn("EMBER_YOUTUBE_COOKIES", SIGNIN_BROKEN_MESSAGE)
        self.assertNotIn("you ", SIGNIN_BROKEN_MESSAGE.lower())


class RefusalClassificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def use(self, text):
        path = self.directory / "cookies.txt"
        path.write_text(text)
        return patch.dict(os.environ, {"EMBER_YOUTUBE_COOKIES": str(path)})

    def test_an_expired_signin_explains_a_refusal(self):
        past = int(time.time()) - 3600
        with self.use(cookie_line("SAPISID", expiry=past)):
            self.assertTrue(refusal_is_signin(REFUSAL))

    def test_a_healthy_signin_leaves_a_private_video_unexplained(self):
        with self.use(cookie_line("SAPISID")):
            self.assertFalse(refusal_is_signin(REFUSAL))

    def test_an_unrelated_failure_is_never_blamed_on_signin(self):
        past = int(time.time()) - 3600
        with self.use(cookie_line("SAPISID", expiry=past)):
            self.assertFalse(
                refusal_is_signin("YouTube returned an unsupported or restricted audio stream.")
            )

    def test_no_configured_signin_is_not_called_broken(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(refusal_is_signin(REFUSAL))


if __name__ == "__main__":
    unittest.main()
