# ADR-002: H5P Packaging Strategy

**Status:** Accepted  
**Date:** May 2026  
**Author:** Yats (yats0x7)

---

## Context

H5P is the output format for all quiz and interactive content in the Shiksha platform. H5P packages (`.h5p` files) are ZIP archives with a strict internal structure. The Sunbird Editor used in `mfes/editors` will **silently fail to load** any H5P package with a malformed manifest or content JSON.

Common failure modes discovered during research:
1. `mainLibrary` field doesn't match pattern `^H5P\.` → package rejected
2. `preloadedDependencies` missing `minorVersion` → editor loads blank
3. `subContentId` missing from question → H5P quiz renders 0 questions
4. Library version mismatch (e.g., `H5P.MultiChoice 1.15` when server has `1.16`) → content unloadable
5. JSON with trailing commas → `JSON.parse` fails silently in some H5P readers

These are not obvious failures — they produce blank screens or silent errors with no useful debug output.

---

## Decision

**H5P packages are generated in pure Python using `zipfile` + `jsonschema` validation, with a mandatory pre-packaging validation gate.**

No external H5P Node.js library is used in the Python backend.

---

## Package Structure (Required)

```
quiz-{task_id}.h5p          (ZIP file)
├── h5p.json                 ← Library manifest (validated against schema)
├── content/
│   └── content.json         ← Question set content (type-specific)
└── (Library folders not required if LMS has them cached)
```

**`h5p.json` minimum valid structure:**
```json
{
  "title": "Quiz Title",
  "mainLibrary": "H5P.QuestionSet",
  "language": "en",
  "embedTypes": ["iframe"],
  "license": "U",
  "preloadedDependencies": [
    { "machineName": "H5P.QuestionSet", "majorVersion": 1, "minorVersion": 20 },
    { "machineName": "H5P.MultiChoice", "majorVersion": 1, "minorVersion": 16 }
  ]
}
```

---

## Validation Gate

Every H5P package is validated **before** being written to disk. This prevents partial/corrupt packages from reaching the Sunbird Editor.

```python
# services/h5p_packager.py
def package_quiz(self, quiz_data, output_path, title):
    manifest = self._build_manifest(title)
    content = self._build_question_set_content(quiz_data, title)
    
    # FAIL FAST — validate before writing
    self._validate_manifest(manifest)    # jsonschema check
    self._validate_content(content)      # structural check
    
    self._write_h5p_zip(manifest, content, output_path)
```

---

## Consequences

**Positive:**
- No Node.js dependency in Python backend (simpler Docker images)
- Full control over H5P structure (can support future H5P types)
- Pre-packaging validation catches LLM output errors before they reach UI
- Pure Python = easier testing (pytest, no browser required)

**Negative:**
- Must manually track H5P library versions (H5P spec updates require code changes)
- Cannot use H5P's own bundled validation tools
- Library folder inclusion (for offline-first LMS) requires manual management

---

## Alternatives Considered

### 1. h5p-nodejs-library (npm package)
**Rejected for backend:** Would require a Node.js process inside the Python backend container, complicating Docker setup and adding a process management layer. However, this library **is appropriate** for the future Node.js micro-lesson builder (Module D).

### 2. Direct LLM → H5P JSON (no intermediate format)
**Rejected:** LLM output cannot be trusted to directly produce H5P-valid JSON due to hallucinated fields, wrong version numbers, and non-compliant structure. An intermediate normalized format (our quiz_data dict) + deterministic packager is safer.

### 3. SCORM-only (skip H5P)
**Rejected:** H5P is required by the DMP ticket and Sunbird Editor. SCORM is generated as an additional wrapper around the H5P package for LMS systems without the H5P plugin.

---

## Version Pinning

Library versions pinned in `h5p_packager.py`:

| Library | Major | Minor |
|---------|-------|-------|
| H5P.QuestionSet | 1 | 20 |
| H5P.MultiChoice | 1 | 16 |
| H5P.Blanks | 1 | 14 |
| H5P.TrueFalse | 1 | 8 |
| FontAwesome | 4 | 5 |
| H5P.JoubelUI | 1 | 3 |

These versions are verified against H5P Hub (2024). Update this table when upgrading H5P Core.