from factory_common.locks import relpath_intersects_scope


def test_relpath_intersects_scope_non_glob_parent_child() -> None:
    scope = "workspaces/scripts/CH04/018/audio_prep"
    assert relpath_intersects_scope("workspaces/scripts/CH04/018/audio_prep", scope)
    assert relpath_intersects_scope("workspaces/scripts/CH04/018/audio_prep/chunks", scope)
    assert relpath_intersects_scope("workspaces/scripts/CH04/018", scope)
    assert not relpath_intersects_scope("workspaces/scripts/CH04/019/audio_prep", scope)


def test_relpath_intersects_scope_glob_prefix() -> None:
    scope = "workspaces/video/runs/**"
    assert relpath_intersects_scope("workspaces/video/runs/CH01-001", scope)
    assert relpath_intersects_scope("workspaces/video/runs", scope)
    assert relpath_intersects_scope("workspaces/video", scope)
    assert not relpath_intersects_scope("workspaces/audio/final/CH01/001", scope)


def test_relpath_intersects_scope_glob_file_pattern() -> None:
    scope = "apps/ui-frontend/src/pages/*.tsx"
    assert relpath_intersects_scope("apps/ui-frontend/src/pages/AgentOrgPage.tsx", scope)
    assert relpath_intersects_scope("apps/ui-frontend/src/pages", scope)
    assert not relpath_intersects_scope("apps/ui-frontend/src/pages/AgentOrgPage.css", scope)

