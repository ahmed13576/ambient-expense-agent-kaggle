# Copyright 2026 Google LLC
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

import re

# PII regex patterns
SSN_REGEX = re.compile(r'\b\d{3}[-\s]\d{2}[-\s]\d{4}\b')
CC_REGEX = re.compile(r'\b(?:\d{4}[-\s]?){3}\d{4}\b')
AMEX_REGEX = re.compile(r'\b\d{4}[-\s]?\d{6}[-\s]?\d{5}\b')

# Prompt injection regex patterns
INJECTION_KEYWORDS = [
    # Override commands
    r'ignore\s+(?:all\s+)?(?:previous|above|prior|initial)\s+(?:instructions|rules|directives|prompts|system)',
    r'disregard\s+(?:all\s+)?(?:previous|above|prior|initial)\s+(?:instructions|rules|directives|prompts|system)',
    r'forget\s+(?:all\s+)?(?:previous|above|prior|initial)\s+(?:instructions|rules|directives|prompts|system)',
    # System role injection markers
    r'system\s*:',
    r'\[\s*system\s*\]',
    r'<\s*system\s*>',
    r'new\s+instruction\s*:',
    # Direct behavior forcing
    r'auto-approve\s+this',
    r'approve\s+this\s+expense',
    r'bypass\s+the\s+(?:rules|checks|model|review|threshold)',
    r'override\s+the\s+(?:rules|checks|model|review|threshold)',
    r'you\s+must\s+(?:approve|auto-approve)',
    r'you\s+are\s+now\s+(?:an\s+approval|admin|system|reviewer)',
    r'pretend\s+you\s+are',
    r'act\s+as\s+(?:an\s+approval|admin|system|reviewer)'
]

INJECTION_PATTERNS = [re.compile(p, re.IGNORECASE) for p in INJECTION_KEYWORDS]


def scrub_pii(text: str) -> tuple[str, list[str]]:
    """Scrubs SSNs and Credit Card numbers from text.
    
    Returns:
        A tuple of (scrubbed_text, list_of_redacted_categories).
    """
    redacted_categories = []
    scrubbed = text

    # Check for SSN
    if SSN_REGEX.search(scrubbed):
        scrubbed = SSN_REGEX.sub("[REDACTED-SSN]", scrubbed)
        redacted_categories.append("SSN")

    # Check for CC
    has_cc = False
    if CC_REGEX.search(scrubbed):
        scrubbed = CC_REGEX.sub("[REDACTED-CC]", scrubbed)
        has_cc = True
    if AMEX_REGEX.search(scrubbed):
        scrubbed = AMEX_REGEX.sub("[REDACTED-CC]", scrubbed)
        has_cc = True

    if has_cc:
        redacted_categories.append("credit-card")

    return scrubbed, redacted_categories


def detect_injection(text: str) -> bool:
    """Detects common prompt injection patterns in the text."""
    for pattern in INJECTION_PATTERNS:
        if pattern.search(text):
            return True
    return False
