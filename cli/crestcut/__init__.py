"""crestcut — a decoupled CLI for the 浪 LIVE highlight-detection + clip editor.

Wave-crest → the peak moment → *cut* into a short clip.

This package is a standalone, stdlib-only HTTP client bound to the 浪 LIVE
contract (``contracts/openapi.yaml``). It has zero import edges into
``backend-api`` and no AWS SDK: the deployed API Gateway/Lambda backend is the
AWS boundary, and the CLI reaches AWS only through the API + presigned URLs.
That makes it portable — point it at any backend that speaks the contract.
"""

__version__ = "0.1.0"
