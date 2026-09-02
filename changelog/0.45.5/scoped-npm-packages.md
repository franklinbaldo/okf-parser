---
type: Release Note
title: publish the npm packages under the @franklinbaldo scope
---

- Rename all three npm packages to `@franklinbaldo/okf-parser`, `@franklinbaldo/okf-parser-duckdb` and `@franklinbaldo/okf-parser-native-linux-x64`. npm refuses the unscoped name `okf-parser` because its similarity filter considers it too close to the existing `oxc-parser`; the refusal happens on upload, so name availability alone never proved the name was publishable.
- Keep the PyPI distribution unscoped as `okf-parser`. The Python and npm names are now deliberately different, because only npm imposes the similarity constraint.
- Update the source contract to expect the scoped names and the tarball file names `npm pack` derives from them, where the scope is flattened into the leading `franklinbaldo-` segment.
- Resolve the optional native package through its scoped name, so a Linux x64 consumer still finds the packaged engine.
