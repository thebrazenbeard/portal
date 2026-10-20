# VeraOS actual-image selection check (SOURCE-ONLY)

The standalone `scripts/audit_veraos_image_selection.py` reads an existing
on-disk image without mutating the image or rebuilding it. It streams the whole
image SHA256 and compares it to the preserved sidecar digest, checks the
selected four-component staging receipt's internal 431-file inventory, and
compares two **different scope** contracts: the historical Buildroot 2024
boot contract and the resident Buildroot 2025 source candidate.

The script returns exit **2** on a successfully inspected but **unselected**
artifact, exit **3** on malformed, mismatched or unreadable evidence. It NEVER
returns a boot-ready or deployment-ready decision. In particular, the image
sidecar and stage receipt are independent inputs: neither proves the older image
was built from the selected new stage. The image is neither mounted nor booted.

A future positively selectable build requires a new versioned, image-specific
resident profile contract and correct post-build installation, exact Buildroot
source/toolchain and builder provenance, measured final `output/images` image
bytes, independent packed-stage equality, and a verified image consumer handoff.
Guest boot, state persistence, `QualifiedVeraRuntime` route/consumption,
and protected effects remain separate qualification gates.

Do not edit the legacy 2024 contract to impersonate the 2025 candidate. Do not
claim the retired NixOS value in that historical contract retires the primary
NixOS candidate shown in the current VeraOS README.

Example (adjust paths for the authorized workstation):

```
python -m scripts.audit_veraos_image_selection \
  --legacy-boot-contract /vera-os/architecture/os/VERA_OS_BOOT_CONTRACT_V1.json \
  --resident-candidate /vera-os/architecture/os/VERA_OS_RESIDENT_BUILDROOT_CANDIDATE_V1.json \
  --selected-stage-receipt /stage/VERA_OS_RESIDENT_COMPONENTS_BUILD_RECEIPT_V1.json \
  --real-historical-image /artifacts/veraos-rootfs.ext2 \
  --image-sha256-sidecar /artifacts/veraos-rootfs.ext2.sha256
```

This cross-repository observer belongs to PORTAL's independent verification
lane; the prospective 2025 resident contract/validator/post-build work is
Integration-owned in VeraOS.
