# Third-party notices

## Agent Zero

Rezztor EAgent is a derivative work of Agent Zero. MIT permits that on one condition: the copyright and permission
notice below stays in every copy. Removing it puts the product outside that licence;
`tests/test_attribution_is_retained.py` keeps it here.

```
MIT License

Copyright (c) 2025 Agent Zero, s.r.o

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Microsoft azure-skills

`plugins/_ea_azure/skills/azure-microsoft-guidance/reference/` holds Markdown from
<https://github.com/microsoft/azure-skills>, © Microsoft Corporation, used under the MIT licence. Its full text is in
`plugins/_ea_azure/skills/azure-microsoft-guidance/reference/LICENSE` and ships with the files. The commit, its date and a
SHA-256 for every vendored file are in `reference/VENDOR.json`. The files are reference text only; nothing is fetched from
Microsoft at run time (ADR 0076).
