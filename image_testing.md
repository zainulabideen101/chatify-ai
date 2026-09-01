# Screenshot Payment Verification Testing Playbook

## Image handling rules
- Use base64-encoded JPEG, PNG, or WEBP only
- Do not use blank, solid-color, SVG, BMP, HEIC, or unsupported images
- Resize large images to reasonable bounds
- Re-detect MIME type after any image conversion
- Use real visual features in test screenshots

## Verification safety
- Test with synthetic placeholder receipts only
- Never mark a real order paid during automated tests
- Verify tenant isolation, structured confidence/reasons, mismatch handling, and human-review fallback
- Confirm the customer-facing flow does not reveal internal AI/human verification labels
