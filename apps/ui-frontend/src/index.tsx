import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import './index.css';
import App from './App';
import MockGallery from './mock/MockGallery';
import reportWebVitals from './reportWebVitals';

const root = ReactDOM.createRoot(
  document.getElementById('root') as HTMLElement
);
const searchParams = new URLSearchParams(window.location.search);
const mockMode = searchParams.get('mock') === '1';

// basenameを動的に設定
const getRouterBasename = (): string => {
  const currentPath = window.location.pathname;
  const basePaths = ['/youtube_production2', '/ui'];
  for (const basePath of basePaths) {
    if (currentPath === basePath || currentPath.startsWith(`${basePath}/`)) {
      return basePath;
    }
  }
  return '/';
};

root.render(
  <React.StrictMode>
    {mockMode ? (
      <MockGallery />
    ) : (
      <BrowserRouter basename={getRouterBasename()}>
        <App />
      </BrowserRouter>
    )}
  </React.StrictMode>
);

// If you want to start measuring performance in your app, pass a function
// to log results (for example: reportWebVitals(console.log))
// or send to an analytics endpoint. Learn more: https://bit.ly/CRA-vitals
reportWebVitals();
