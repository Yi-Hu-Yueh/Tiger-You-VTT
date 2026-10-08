# License and redistribution review

Review scope: accepted PC/Web, Android client and Android standalone source,
their build inputs and the privately tested standalone v0.4.1 APK. This is an
engineering inventory, not legal advice or a legal guarantee. Owner/private
acceptance is PASS; **public APK redistribution readiness is not asserted**.

## Actual licenses and source pins

| Component | Reviewed source / terms | Required treatment |
|---|---|---|
| Tiger-You-VTT original source | Root `LICENSE`, MIT, Copyright 2026 Tiger (樂以虎@Taiwan) | Preserve copyright and license. The MIT grant is not a statement that the combined APK is MIT-only. |
| sherpa-onnx 1.13.8 | [Pinned Apache-2.0 license](https://github.com/k2-fsa/sherpa-onnx/blob/v1.13.8/LICENSE), local `sherpa-onnx-LICENSE.txt` | Preserve applicable copyright, license and upstream NOTICE; identify modifications if any. Runtime AAR is used unmodified and hash-pinned. |
| ONNX Runtime | MIT, APK `onnxruntime-LICENSE.txt` | Retain attribution and license; audit all native runtime notices for any public binary release. |
| SenseVoice Small INT8 | [FunASR MODEL_LICENSE at recorded source revision](https://github.com/modelscope/FunASR/blob/58830eca4012644aac0c3218c3ccc7d98f003fda/MODEL_LICENSE), local full bilingual model license | Custom FunASR Model Open Source License Agreement v1.1, not Apache/MIT. Preserve author and model/source identification; review its use restrictions, termination and update clauses. Model data is not silently relicensed as GPL. |
| NewPipe Extractor v0.26.5 | [Pinned source](https://github.com/TeamNewPipe/NewPipeExtractor/tree/f9e6bb808f82bf3e4dc1f2a29a16fd376931c8ef), local `NewPipe-GPL-3.0.txt` and `NewPipe-SOURCE.txt` | GPL-3.0-or-later. Linked standalone application distribution must address GPL requirements for the combined work and corresponding source, not just include a link to NewPipe. |

NewPipe coordinates: `com.github.teamnewpipe:NewPipeExtractor:v0.26.5`;
revision `f9e6bb808f82bf3e4dc1f2a29a16fd376931c8ef`. No upstream extractor
modifications. Model conversion repository is
`csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17` at
`2365baeacb507f821a0c8120fcee3d484dba7a07`; original authors are
FunAudioLLM / Alibaba Group; conversion is distributed by csukuangfj / sherpa-onnx.
APK assets retain model license and attribution; runtime and weights are distinct.

## Dependency notices

The checked-in `android-standalone/app/src/main/assets/licenses/` directory
contains the primary notices and a dependency/source inventory. The build pins
OkHttp 4.12.0 and jsoup 1.22.2. NewPipe's dependency metadata identifies nanojson
`e9d656ddb49a412a5a0a5d5ef20ca7ef09549996` (MIT), jsr305 3.0.2 (Apache-2.0),
protobuf-javalite 4.35.1 (BSD-3-Clause), and Rhino / Rhino engine 1.8.1
(MPL-2.0). Okio is Apache-2.0. AndroidX/Compose and Kotlin notices also apply.
Core library desugaring uses `desugar_jdk_libs_nio:2.1.5`, including OpenJDK
GPLv2 with Classpath Exception and other notices. Keep resolved dependency
metadata and upstream notice texts with release records. This inventory does
not assert that every transitive notice is already packaged for public release.

## Public binary release gate

Before offering an APK to the public, a maintainer must:

1. Review GPL applicability to the combined Android application with appropriate
   legal expertise, including compatibility of separate model terms. Retain the
   original Tiger MIT attribution and all third-party terms; do not relabel the
   model or upstream code. No repository-wide relicense is made by this review.
2. Provide the complete corresponding source for the distributed GPL-covered
   work, including Tiger integration, exact dependency sources/modifications,
   interface/build scripts and required installation information where applicable.
   A NewPipe homepage link or this repository alone is not a complete source offer.
3. Choose and actually implement a GPL section 6-compliant conveyance method
   (for example equivalent network access to corresponding source alongside
   the binary), verify recipients' access, and retain versioned source archives.
   This document is **not** an unfulfilled written source offer.
4. Preserve licenses, copyright and NOTICE files for runtime and all transitive
   dependencies, including MPL file-level source obligations and BSD notices.
   Review model license conditions separately. Confirm any app-store conditions
   do not impose incompatible restrictions.
5. Use a deliberate release signing process, verify binary/source correspondence,
   and keep signing keys, cookies, tokens and build caches outside Git.

Closure commits source and documentation, not APKs or model weights. The retained
debug APKs are private validation artifacts, not a public release announcement.
YouTube terms, content permissions and platform capture restrictions still apply;
successful extraction does not grant rights to redistribute third-party content.
