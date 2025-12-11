import { Navigate, Route, Routes } from "react-router-dom";
import "./App.css";
import { AppShell } from "./layouts/AppShell";
import { DashboardPage } from "./pages/DashboardPage";
import { ScriptFactoryPage } from "./pages/ScriptFactoryPage";
import { ChannelOverviewPage } from "./pages/ChannelOverviewPage";
import { ChannelDetailPage } from "./pages/ChannelDetailPage";
import { ChannelWorkspacePage } from "./pages/ChannelWorkspacePage";
import { ChannelSettingsPage } from "./pages/ChannelSettingsPage";
import { ResearchPage } from "./pages/ResearchPage";
import { ThumbnailsPage } from "./pages/ThumbnailsPage";
import { AudioReviewRoute } from "./pages/AudioReviewRoute";
import { ReportsPage } from "./pages/ReportsPage";
import { PromptManagerPage } from "./pages/PromptManagerPage";
import { SettingsPage } from "./pages/SettingsPage";
import { AudioTtsV2Page } from "./pages/AudioTtsV2Page";
import { JobsPage } from "./pages/JobsPage";
import { CapcutEditPage } from "./pages/CapcutEditPage";
import { CapcutDraftPage } from "./pages/CapcutDraftPage";
import { CapcutSwapPage } from "./pages/CapcutSwapPage";
import TtsProgressPage from "./pages/TtsProgressPage";
import { AudioIntegrityPage } from "./pages/AudioIntegrityPage";
import LlmUsagePage from "./pages/LlmUsagePage";

function App() {
  return (
    <Routes>
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route element={<AppShell />}>
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/projects" element={<ScriptFactoryPage />} />
        <Route path="/channel-workspace" element={<ChannelWorkspacePage />} />
        <Route path="/channel-settings" element={<ChannelSettingsPage />} />
        <Route path="/prompts" element={<PromptManagerPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/channels/:channelCode" element={<ChannelOverviewPage />} />
        <Route path="/channels/:channelCode/videos/:video" element={<ChannelDetailPage />} />
        <Route path="/research" element={<ResearchPage />} />
        <Route path="/thumbnails" element={<ThumbnailsPage />} />
        <Route path="/audio-review" element={<AudioReviewRoute />} />
        <Route path="/capcut-edit" element={<CapcutEditPage />} />
        <Route path="/capcut-edit/draft" element={<CapcutDraftPage />} />
        <Route path="/capcut-edit/swap" element={<CapcutSwapPage />} />
        <Route path="/audio-tts-v2" element={<AudioTtsV2Page />} />
        <Route path="/audio-integrity" element={<AudioIntegrityPage />} />
        <Route path="/tts-progress" element={<TtsProgressPage />} />
        <Route path="/jobs" element={<JobsPage />} />
        <Route path="/reports" element={<ReportsPage />} />
        <Route path="/llm-usage" element={<LlmUsagePage />} />
      </Route>
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}

export default App;
