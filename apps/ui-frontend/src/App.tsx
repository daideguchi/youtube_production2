import "./App.css";
import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./layouts/AppShell";
import { DashboardPage } from "./pages/DashboardPage";
import { PublishingProgressPage } from "./pages/PublishingProgressPage";
import { WorkflowPage } from "./pages/WorkflowPage";
import { ScriptFactoryPage } from "./pages/ScriptFactoryPage";
import { ChannelOverviewPage } from "./pages/ChannelOverviewPage";
import { ChannelPortalPage } from "./pages/ChannelPortalPage";
import { ChannelDetailPage } from "./pages/ChannelDetailPage";
import { ChannelWorkspacePage } from "./pages/ChannelWorkspacePage";
import { ChannelSettingsPage } from "./pages/ChannelSettingsPage";
import { BenchmarksPage } from "./pages/BenchmarksPage";
import { ResearchPage } from "./pages/ResearchPage";
import { SsotPortalPage } from "./pages/SsotPortalPage";
import { SsotMapPage } from "./pages/SsotMapPage";
import { SsotEntrypointsPage } from "./pages/SsotEntrypointsPage";
import { SsotTracePage } from "./pages/SsotTracePage";
import { SsotGapsPage } from "./pages/SsotGapsPage";
import { SsotZombiesPage } from "./pages/SsotZombiesPage";
import { ThumbnailsPage } from "./pages/ThumbnailsPage";
import { ThumbnailsMobilePage } from "./pages/ThumbnailsMobilePage";
import { AudioReviewRoute } from "./pages/AudioReviewRoute";
import { ReportsPage } from "./pages/ReportsPage";
import { PromptManagerPage } from "./pages/PromptManagerPage";
import { SettingsPage } from "./pages/SettingsPage";
import { AudioTtsPage } from "./pages/AudioTtsPage";
import { JobsPage } from "./pages/JobsPage";
import { CapcutEditPage } from "./pages/CapcutEditPage";
import { CapcutDraftPage } from "./pages/CapcutDraftPage";
import { CapcutSwapPage } from "./pages/CapcutSwapPage";
import { ProductionPage } from "./pages/ProductionPage";
import TtsProgressPage from "./pages/TtsProgressPage";
import { AudioIntegrityPage } from "./pages/AudioIntegrityPage";
import LlmUsagePage from "./pages/LlmUsagePage";
import LlmUsageDashboardPage from "./pages/LlmUsageDashboardPage";
import { PlanningPage } from "./pages/PlanningPage";
import { DictionaryPage } from "./pages/DictionaryPage";
import { EpisodeStudioPage } from "./pages/EpisodeStudioPage";
import { AgentOrgPage } from "./pages/AgentOrgPage";
import { AgentBoardPage } from "./pages/AgentBoardPage";
import { RemotionWorkspacePage } from "./pages/RemotionWorkspacePage";
import { ImageManagementPage } from "./pages/ImageManagementPage";
import { AuditPage } from "./pages/AuditPage";
import { ImageModelRoutingPage } from "./pages/ImageModelRoutingPage";
import { ChannelModelPolicyPage } from "./pages/ChannelModelPolicyPage";
import { GhReleasesArchivePage } from "./pages/GhReleasesArchivePage";

function App() {
  return (
    <Routes>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route element={<AppShell />}>
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/publishing-progress" element={<PublishingProgressPage />} />
        <Route path="/audit" element={<AuditPage />} />
        <Route path="/qc" element={<Navigate to="/audit" replace />} />
        <Route path="/workflow" element={<WorkflowPage />} />
        <Route path="/studio" element={<EpisodeStudioPage />} />
        <Route path="/projects" element={<ScriptFactoryPage />} />
        <Route path="/channel-workspace" element={<ChannelWorkspacePage />} />
        <Route path="/channel-settings" element={<ChannelSettingsPage />} />
        <Route path="/prompts" element={<PromptManagerPage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/model-policy" element={<ChannelModelPolicyPage />} />
        <Route path="/image-model-routing" element={<ImageModelRoutingPage />} />
        <Route path="/channels/:channelCode" element={<ChannelOverviewPage />} />
        <Route path="/channels/:channelCode/portal" element={<ChannelPortalPage />} />
        <Route path="/channels/:channelCode/videos/:video" element={<ChannelDetailPage />} />
        <Route path="/benchmarks" element={<BenchmarksPage />} />
        <Route path="/research" element={<ResearchPage />} />
        <Route path="/ssot" element={<SsotPortalPage />} />
        <Route path="/ssot/map" element={<SsotMapPage />} />
        <Route path="/ssot/gaps" element={<SsotGapsPage />} />
        <Route path="/ssot/zombies" element={<SsotZombiesPage />} />
        <Route path="/ssot/entrypoints" element={<SsotEntrypointsPage />} />
        <Route path="/ssot/trace" element={<SsotTracePage />} />
        <Route path="/ssot/trace/:key" element={<SsotTracePage />} />
        <Route path="/thumbnails/mobile" element={<ThumbnailsMobilePage />} />
        <Route path="/thumbnails" element={<ThumbnailsPage />} />
        <Route path="/image-management" element={<ImageManagementPage />} />
        <Route path="/image-timeline" element={<ImageManagementPage />} />
        <Route path="/dictionary" element={<DictionaryPage />} />
        <Route path="/agent-org" element={<AgentOrgPage />} />
        <Route path="/agent-board" element={<AgentBoardPage />} />
        <Route path="/archives" element={<GhReleasesArchivePage />} />
        <Route path="/audio-review" element={<AudioReviewRoute />} />
        <Route path="/capcut-edit" element={<CapcutEditPage />} />
        <Route path="/capcut-edit/production" element={<ProductionPage />} />
        <Route path="/capcut-edit/draft" element={<CapcutDraftPage />} />
        <Route path="/capcut-edit/swap" element={<CapcutSwapPage />} />
        <Route path="/video-remotion" element={<RemotionWorkspacePage />} />
        <Route path="/audio-tts" element={<AudioTtsPage />} />
        <Route path="/audio-integrity" element={<AudioIntegrityPage />} />
        <Route path="/audio-integrity/:channel/:video" element={<AudioIntegrityPage />} />
        <Route path="/tts-progress" element={<TtsProgressPage />} />
        <Route path="/planning" element={<PlanningPage />} />
        <Route path="/jobs" element={<JobsPage />} />
        <Route path="/reports" element={<ReportsPage />} />
        <Route path="/llm-usage" element={<LlmUsagePage />} />
        <Route path="/llm-usage/dashboard" element={<LlmUsageDashboardPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/dashboard" replace />} />
    </Routes>
  );
}

export default App;
