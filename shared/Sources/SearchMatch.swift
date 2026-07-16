// SearchMatch.swift — normalized board-search matching (CONTRACT §37).
// SHARED between the Mac app and the iOS app. Foundation-only by contract
// (mac/build.sh lint gate). Pure value logic so the contract harness
// (ios/tests/contract) can exercise it with plain swiftc.
//
// Matching rules (§37, frozen):
//  - separator-free: "-", "_", "." and all whitespace are stripped from BOTH
//    sides before substring comparison, so "eb1" finds "EB-1A" and "h1b"
//    finds "H-1B" — while "eb2" still does NOT find "EB-1A";
//  - CJK matches as a plain substring (normalization never drops CJK);
//  - the query is whitespace-split into terms with AND semantics: every term
//    must match at least one field of the card;
//  - case-insensitive throughout. An empty/whitespace query matches all
//    (filtering off — the store's passthrough guard).

import Foundation

enum SearchMatch {
    /// Lowercase + strip separators ("-", "_", ".", whitespace) so latin/digit
    /// runs compare separator-free; CJK and everything else passes through.
    static func normalize<S: StringProtocol>(_ s: S) -> String {
        var out = String()
        out.reserveCapacity(s.count)
        for ch in s.lowercased() {
            if ch.isWhitespace || ch == "-" || ch == "_" || ch == "." { continue }
            out.append(ch)
        }
        return out
    }

    /// Whitespace-split, normalized, non-empty query terms (AND semantics).
    static func terms(_ query: String) -> [String] {
        query.split(whereSeparator: { $0.isWhitespace })
            .map { normalize($0) }
            .filter { !$0.isEmpty }
    }

    /// True when EVERY query term matches (normalized substring) at least one
    /// of `fields`. Empty query / empty terms = true (filtering off).
    /// Convenience over `matchesNormalized` — hot paths (per-keystroke board
    /// filtering) should pre-normalize the haystack ONCE per card and call
    /// the normalized variant instead (review fix: the old lazy haystack
    /// re-normalized every field once per term).
    static func matches(_ query: String, in fields: [String]) -> Bool {
        matchesNormalized(query, in: normalizedHaystack(fields))
    }

    /// Materialize the normalized haystack once (cacheable by callers).
    static func normalizedHaystack(_ fields: [String]) -> [String] {
        fields.map { normalize($0) }.filter { !$0.isEmpty }
    }

    /// Hot-path variant over an ALREADY-normalized haystack (see
    /// `normalizedHaystack`). Empty query / empty terms = true.
    static func matchesNormalized(_ query: String, in normalizedFields: [String]) -> Bool {
        let ts = terms(query)
        guard !ts.isEmpty else { return true }
        return ts.allSatisfy { t in normalizedFields.contains { $0.contains(t) } }
    }
}
