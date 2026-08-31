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
}
