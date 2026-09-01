// RuntimePathsTests.swift — §55 渲染前置：物理路径 + 验证过的解释器。
//
// 2026-08-31 live 部署事故：owner 的 repo 实体在 /Volumes/… 上，另有便利
// symlink ~/Projects -> /Volumes/Storage/Server/Projects。App 的两个渲染方
// （Doctor.LaunchAgents.install / SetupWizard.ActdAgent.renderAndLoad）当时
// 直接拿 AppPaths.stateRoot 去替换占位符，于是 symlink 形状被烧进 plist 的
// PYTHONPATH / AIASSISTANT_HOME —— launchd 起的进程经该形状被 TCC 拒绝，
// 每次 spawn 都以 `ModuleNotFoundError: No module named 'act'` 退出 1。
// 第二个症状是同一轮渲染挑中了没有 PyYAML 的 python3。
//
// 两个渲染方本身要 AppKit/launchctl，进不了这个包；这里钉的是它们共用的两个
// 纯函数：AppPaths.physical 与 RuntimePython.importsYAML（后者只喂 /bin/sh
// 假壳，绝不起真 python）。install.sh 那一侧的对称判例在
// tests/test_launchd_render.py InstallShRealRenderTestCase。

import Testing
import Foundation
@testable import MacLogic

@Suite
struct RuntimePathsTests {

    private func tempDir(_ prefix: String) throws -> String {
        let dir = NSTemporaryDirectory() + prefix + UUID().uuidString
        try FileManager.default.createDirectory(
            atPath: dir, withIntermediateDirectories: true)
        return dir
    }

    @Test("a symlinked repo path resolves to its physical location")
    func physicalResolvesSymlinks() throws {
        let root = try tempDir("physical-")
        defer { try? FileManager.default.removeItem(atPath: root) }
        let real = root + "/physical/repo"
        try FileManager.default.createDirectory(
            atPath: real, withIntermediateDirectories: true)
        try FileManager.default.createSymbolicLink(
            atPath: root + "/shortcut", withDestinationPath: root + "/physical")

        let linked = root + "/shortcut/repo"
        let resolved = AppPaths.physical(linked)
        #expect(resolved != linked)
        #expect(resolved == AppPaths.physical(real))
        #expect(!resolved.contains("/shortcut/"))
    }

    @Test("resolving an already-physical path is a no-op")
    func physicalIsIdempotent() throws {
        let root = try tempDir("idempotent-")
        defer { try? FileManager.default.removeItem(atPath: root) }
        let once = AppPaths.physical(root)
        #expect(AppPaths.physical(once) == once)
    }

    @Test("an empty path stays empty rather than becoming the cwd")
    func physicalKeepsEmptyEmpty() {
        #expect(AppPaths.physical("") == "")
    }

    @Test("only an absolute, executable interpreter that imports yaml passes")
    func importsYAMLValidates() throws {
        let dir = try tempDir("interp-")
        defer { try? FileManager.default.removeItem(atPath: dir) }

        func shim(_ name: String, _ exitCode: Int) throws -> String {
            let path = dir + "/" + name
            try "#!/bin/sh\nexit \(exitCode)\n".write(
                toFile: path, atomically: true, encoding: .utf8)
            try FileManager.default.setAttributes(
                [.posixPermissions: 0o755], ofItemAtPath: path)
            return path
        }

        #expect(RuntimePython.importsYAML(try shim("good-python3", 0)))
        #expect(!RuntimePython.importsYAML(try shim("no-yaml-python3", 1)))
        #expect(!RuntimePython.importsYAML(dir + "/missing-python3"))
        #expect(!RuntimePython.importsYAML("python3"),  // relative — TCC drifts
                "a bare name must never reach a plist")
        #expect(!RuntimePython.importsYAML(""))
    }

    // MARK: - §55 第二道闸门：launchd 可行性
    //
    // 同一次事故的最后一幕：路径修好之后 agent 仍然全部
    // `No module named 'act'`。plist 里那个 /opt/homebrew/bin/python3 确实装着
    // PyYAML（yaml 闸门放它过了），但 macOS **按 binary** 授文件访问权，而
    // launchd job 是它自己的 responsible process，不继承 App/终端的授权——
    // 它读不了 /Volumes/… 上的 repo，/usr/bin/python3 读得了。
    // 这里钉纯逻辑的两块：候选次序 + 双闸门选择（探针全部注入，绝不起 launchd）。

    @Test("the system python is probed first when the repo is outside $HOME")
    func systemPythonRanksFirstOutsideHome() {
        let order = RuntimePython.candidateOrder(
            outsideHome: true, pin: "/opt/homebrew/bin/python3",
            shellPython: "/opt/homebrew/bin/python3")
        #expect(order.first == "/usr/bin/python3",
                "outside $HOME is where per-binary TCC bites — the Apple python already carries the user's file grants, so it goes first")
    }

    @Test("inside $HOME the pinned interpreter keeps priority")
    func pinKeepsPriorityInsideHome() {
        let order = RuntimePython.candidateOrder(
            outsideHome: false, pin: "/opt/homebrew/bin/python3",
            shellPython: "/usr/local/bin/python3")
        #expect(order.first == "/opt/homebrew/bin/python3")
        #expect(order.last == "/usr/bin/python3",
                "no TCC boundary inside $HOME — system python stays last resort")
    }

    @Test("outside-$HOME is decided on physical paths, not the symlink shape")
    func outsideHomeResolvesSymlinks() throws {
        // 事故原始形状：~/Projects -> /Volumes/… 的便利 symlink
        let root = try tempDir("outside-")
        defer { try? FileManager.default.removeItem(atPath: root) }
        let home = root + "/home"
        let elsewhere = root + "/elsewhere/repo"
        try FileManager.default.createDirectory(
            atPath: home + "/Projects", withIntermediateDirectories: true)
        try FileManager.default.createDirectory(
            atPath: elsewhere, withIntermediateDirectories: true)
        try FileManager.default.createSymbolicLink(
            atPath: home + "/Projects/link", withDestinationPath: elsewhere)

        #expect(RuntimePython.isOutsideHome(repo: home + "/Projects/link",
                                            home: home),
                "a symlink from $HOME into an outside path is still outside")
        #expect(!RuntimePython.isOutsideHome(repo: home + "/Projects", home: home))
    }

    @Test("a yaml-capable but launchd-blind interpreter is skipped")
    func launchdGateSkipsTheBlindInterpreter() {
        let (pick, note) = RuntimePython.selectForLaunchd(
            ["/opt/homebrew/bin/python3", "/usr/bin/python3"],
            yamlGate: { _ in true },   // 事故本体：两个都能 import yaml
            launchdGate: { $0 == "/usr/bin/python3" })
        #expect(pick == "/usr/bin/python3")
        #expect(note.isEmpty, "a clean pick carries no caveat")
    }

    @Test("the yaml gate still runs first")
    func yamlGateRunsFirst() {
        let (pick, _) = RuntimePython.selectForLaunchd(
            ["/no/yaml/python3", "/good/python3"],
            yamlGate: { $0 == "/good/python3" },
            launchdGate: { _ in true })
        #expect(pick == "/good/python3")
    }

    @Test("falls back to the first yaml candidate when none are launchd-viable")
    func fallsBackWhenNoneAreViable() {
        let (pick, note) = RuntimePython.selectForLaunchd(
            ["/first/python3", "/second/python3"],
            yamlGate: { _ in true },
            launchdGate: { _ in false })
        #expect(pick == "/first/python3",
                "yaml-capable is still strictly better than a PATH guess")
        #expect(note.contains("cannot import act under launchd"))
    }

    @Test("an unavailable probe degrades to the yaml gate, never a rejection")
    func inconclusiveProbeDegrades() {
        // 没有 launchd（CI）时探针返回 nil = 测不出，绝不当成拒绝
        let (pick, note) = RuntimePython.selectForLaunchd(
            ["/only/python3"],
            yamlGate: { _ in true },
            launchdGate: { _ in nil })
        #expect(pick == "/only/python3")
        #expect(note.contains("unverifiable"))
    }

    @Test("nothing yaml-capable yields an empty pick for the caller to report")
    func noYAMLCandidateYieldsEmpty() {
        let (pick, _) = RuntimePython.selectForLaunchd(
            ["/a/python3", "/b/python3"],
            yamlGate: { _ in false },
            launchdGate: { _ in true })
        #expect(pick.isEmpty,
                "resolveForLaunchd falls back to resolve() so the doctor's \"daemon python\" FAIL stays the thing that reports it")
    }
}
