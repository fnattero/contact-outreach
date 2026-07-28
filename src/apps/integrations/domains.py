from __future__ import annotations

import tldextract

# Deliberately offline: the bundled Public Suffix List snapshot is deterministic
# and cannot turn deduplication or SSRF decisions into an external network call.
PUBLIC_SUFFIX_EXTRACTOR = tldextract.TLDExtract(
    suffix_list_urls=(),
    fallback_to_snapshot=True,
    include_psl_private_domains=True,
    cache_dir=None,
)


def registrable_domain_from_hostname(hostname: str) -> str:
    candidate = hostname.rstrip(".").casefold()
    if not candidate:
        return ""
    extracted = PUBLIC_SUFFIX_EXTRACTOR(candidate)
    return extracted.top_domain_under_public_suffix or candidate
