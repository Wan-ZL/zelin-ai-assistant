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
    static func matches(_ query: String, in fields: [String]) -> Bool {
        let ts = terms(query)
        guard !ts.isEmpty else { return true }
        let hay = fields.lazy.map { normalize($0) }.filter { !$0.isEmpty }
        return ts.allSatisfy { t in hay.contains { $0.contains(t) } }
    }
}
