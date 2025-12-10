import React, { useState, useEffect } from "react";
import { useParams } from "react-router-dom";
import { 
  Box, 
  Typography, 
  Paper, 
  Table, 
  TableBody, 
  TableCell, 
  TableContainer, 
  TableHead, 
  TableRow,
  Chip,
  Accordion,
  AccordionSummary,
  AccordionDetails,
  Button,
  TextField,
  IconButton
} from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import ErrorIcon from "@mui/icons-material/Error";
import DeleteIcon from "@mui/icons-material/Delete";
import RefreshIcon from "@mui/icons-material/Refresh";

// API Endpoint Base (Inferred from window.location or hardcoded for dev)
const API_BASE = "http://127.0.0.1:8000/api";

interface AudioSegment {
  text: string;
  reading: string;
  mecab: string;
  voicevox: string;
  verdict: string;
  heading: boolean;
  pre: number;
  post: number;
  duration: number;
}

interface AudioLog {
  channel: string;
  video: string;
  engine: string;
  timestamp: number;
  segments: AudioSegment[];
}

interface KBEntry {
  original: string;
  fixed_text: string;
  source: string;
  timestamp: number;
}

interface KBData {
  version: number;
  entries: Record<string, KBEntry>;
}

const AudioCheck: React.FC = () => {
  const { channelId, videoId } = useParams<{ channelId: string; videoId: string }>();
  const [log, setLog] = useState<AudioLog | null>(null);
  const [kb, setKb] = useState<KBData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchData = async () => {
    setLoading(true);
    setError(null);
    try {
      if (channelId && videoId) {
        const logRes = await fetch(`${API_BASE}/audio-check/${channelId}/${videoId}`);
        if (!logRes.ok) throw new Error("Log not found");
        const logData = await logRes.json();
        setLog(logData);
      }

      const kbRes = await fetch(`${API_BASE}/kb`);
      const kbData = await kbRes.json();
      setKb(kbData);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, [channelId, videoId]);

  const handleDeleteKB = async (key: string) => {
    if (!window.confirm("Delete this entry from Knowledge Base?")) return;
    try {
      await fetch(`${API_BASE}/kb/${key}`, { method: "DELETE" });
      fetchData(); // Refresh
    } catch (e) {
      alert("Failed to delete");
    }
  };

  const getVerdictColor = (verdict: string) => {
    if (verdict.includes("match")) return "success";
    if (verdict.includes("patched") || verdict.includes("fix")) return "warning";
    if (verdict.includes("fallback")) return "error";
    if (verdict.includes("kb")) return "info";
    return "default";
  };

  if (loading) return <Typography>Loading...</Typography>;
  if (error) return <Typography color="error">{error}</Typography>;

  return (
    <Box p={3}>
      <Typography variant="h4" gutterBottom>
        Audio Integrity Check: {channelId}-{videoId}
      </Typography>
      
      <Button startIcon={<RefreshIcon />} onClick={fetchData} variant="outlined" sx={{ mb: 2 }}>
        Refresh
      </Button>

      {log && (
        <Paper sx={{ mb: 4, p: 2 }}>
           <Typography variant="subtitle1">Engine: {log.engine} | Segments: {log.segments.length}</Typography>
           <TableContainer sx={{ maxHeight: 600 }}>
             <Table stickyHeader size="small">
               <TableHead>
                 <TableRow>
                   <TableCell>#</TableCell>
                   <TableCell>Text (Kanji)</TableCell>
                   <TableCell>Final Reading (TTS Input)</TableCell>
                   <TableCell>Status</TableCell>
                   <TableCell>Pause</TableCell>
                 </TableRow>
               </TableHead>
               <TableBody>
                 {log.segments.map((seg, idx) => (
                   <TableRow key={idx} hover sx={{ backgroundColor: seg.heading ? "#f5f5f5" : "inherit" }}>
                     <TableCell>{idx}</TableCell>
                     <TableCell sx={{ maxWidth: 300, wordBreak: "break-all" }}>
                       {seg.heading ? <b>{seg.text}</b> : seg.text}
                       <Box sx={{ color: "text.secondary", fontSize: "0.75rem" }}>
                         MeCab: {seg.mecab}
                       </Box>
                     </TableCell>
                     <TableCell sx={{ maxWidth: 300, wordBreak: "break-all" }}>
                       <span style={{ 
                         color: seg.text !== seg.reading ? "#d32f2f" : "inherit",
                         fontWeight: seg.text !== seg.reading ? "bold" : "normal"
                        }}>
                         {seg.reading}
                       </span>
                       {seg.text !== seg.reading && (
                         <Box sx={{ color: "text.secondary", fontSize: "0.75rem" }}>
                           Original: {seg.voicevox}
                         </Box>
                       )}
                     </TableCell>
                     <TableCell>
                       <Chip 
                         label={seg.verdict} 
                         color={getVerdictColor(seg.verdict) as any} 
                         size="small" 
                         variant="outlined"
                       />
                     </TableCell>
                     <TableCell>
                       {seg.pre > 0 && <Chip label={`Pre: ${seg.pre}s`} size="small" sx={{ mr: 0.5 }} />}
                       {seg.post > 0 && <Chip label={`Post: ${seg.post}s`} size="small" />}
                     </TableCell>
                   </TableRow>
                 ))}
               </TableBody>
             </Table>
           </TableContainer>
        </Paper>
      )}

      <Accordion>
        <AccordionSummary expandIcon={<ExpandMoreIcon />}>
          <Typography variant="h6">Global Knowledge Base ({kb ? Object.keys(kb.entries).length : 0})</Typography>
        </AccordionSummary>
        <AccordionDetails>
          <TableContainer component={Paper} sx={{ maxHeight: 400 }}>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Original</TableCell>
                  <TableCell>Fixed (TTS)</TableCell>
                  <TableCell>Source</TableCell>
                  <TableCell>Action</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {kb && Object.entries(kb.entries).map(([key, entry]) => (
                  <TableRow key={key}>
                    <TableCell>{entry.original}</TableCell>
                    <TableCell>{entry.fixed_text}</TableCell>
                    <TableCell>{entry.source}</TableCell>
                    <TableCell>
                      <IconButton onClick={() => handleDeleteKB(key)} color="error" size="small">
                        <DeleteIcon />
                      </IconButton>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </AccordionDetails>
      </Accordion>
    </Box>
  );
};

export default AudioCheck;
