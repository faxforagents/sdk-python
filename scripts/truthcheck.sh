#!/bin/sh
# truthcheck: fail when a tracked file states something the product does not ship or a price that is
# not on the card. Provenance: faxforagents/openapi scripts/truthcheck.sh (2026-10-28). spec/ is the
# public OpenAPI document, checked in its own repository, so it is exempt here.
#
# The price card (price_version launch-v1, 2026-10-28): $0.039 per page sent, $0.019 per page
# received, $2 per number per month, $19 per month workspace. Any other dollar amount in copy is stale
# or invented. Claims the product does not make: HIPAA, "the first", "cheapest". Competitors are never
# named. CHANGELOG.md is exempt from the price rule (dated entries quote the card as it stood).
set -u
cd "$(dirname "$0")/.." || exit 1

FILES=$(git ls-files | grep -vE '^CHANGELOG\.md$|^scripts/truthcheck\.sh$|^spec/|\.(png|jpg|jpeg|ico|svg|woff2?)$')
[ -n "$FILES" ] || exit 0

fail=0
flag() {
    echo "truthcheck: FAIL: $1" >&2
    fail=1
}

# 1) Dollar amounts other than the four on the card (test code may use $100.000 for the seeded test wallet).
prices=$(printf '%s\n' "$FILES" | grep -vE '^tests/' | xargs grep -noE '\$[0-9][0-9,]*(\.[0-9]+)?' 2>/dev/null \
    | grep -vE ':\$(0\.039|0\.019|2|2\.00|19|19\.00)$' || true)
if [ -n "$prices" ]; then
    printf '%s\n' "$prices" >&2
    flag "a dollar amount that is not on the price card (0.039, 0.019, 2, 19)"
fi

# 2) Claims the product does not make.
if printf '%s\n' "$FILES" | xargs grep -ni 'hipaa' 2>/dev/null; then
    flag "HIPAA is not a claim this product makes"
fi
if printf '%s\n' "$FILES" | xargs grep -niE "(the|world'?s|industry'?s) first (fax|api|provider|service|platform|mcp)|first[- ]ever|first and only" 2>/dev/null; then
    flag "no rank claims (\"the first\")"
fi
if printf '%s\n' "$FILES" | xargs grep -niE 'cheapest|lowest[- ]price|best[- ]price' 2>/dev/null; then
    flag "no price superlatives"
fi

# 3) Competitors are never named on our surfaces.
if printf '%s\n' "$FILES" | xargs grep -niE '\b(phaxio|documo|mfax|efax|srfax|fax\.plus|interfax|ringcentral|faxage|humblefax|westfax|clicksend|sfax|notifyre|ifax|faxburner)\b' 2>/dev/null; then
    flag "a competitor is named"
fi

# 4) No personal handle in an org repo, no Claude attribution, no key material.
if printf '%s\n' "$FILES" | xargs grep -n 'bensynapse' 2>/dev/null; then
    flag "personal handle in an org repo"
fi
if printf '%s\n' "$FILES" | xargs grep -nE 'Co-Authored-By|Generated with|Claude-Session' 2>/dev/null; then
    flag "attribution line"
fi
if printf '%s\n' "$FILES" | xargs grep -nE 'ffa_(live|test)_[A-Za-z0-9_-]{43}' 2>/dev/null | grep -vE '"a" \* 43|aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'; then
    flag "a real-looking API key"
fi

# 5) Anchors that have to be present wherever prices are described.
if printf '%s\n' "$FILES" | xargs grep -lq '\$0\.039' 2>/dev/null; then
    if ! printf '%s\n' "$FILES" | xargs grep -q '2026-10-28' 2>/dev/null; then
        flag "a price is stated without its date (2026-10-28)"
    fi
fi

if [ "$fail" -eq 0 ]; then
    echo "truthcheck: OK"
fi
exit "$fail"
