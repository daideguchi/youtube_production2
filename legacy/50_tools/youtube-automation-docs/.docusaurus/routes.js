import React from 'react';
import ComponentCreator from '@docusaurus/ComponentCreator';

export default [
  {
    path: '/markdown-page',
    component: ComponentCreator('/markdown-page', '3d7'),
    exact: true
  },
  {
    path: '/docs',
    component: ComponentCreator('/docs', '658'),
    routes: [
      {
        path: '/docs',
        component: ComponentCreator('/docs', '07c'),
        routes: [
          {
            path: '/docs',
            component: ComponentCreator('/docs', 'f14'),
            routes: [
              {
                path: '/docs/architecture/overview',
                component: ComponentCreator('/docs/architecture/overview', '833'),
                exact: true,
                sidebar: "tutorialSidebar"
              },
              {
                path: '/docs/intro',
                component: ComponentCreator('/docs/intro', '61d'),
                exact: true,
                sidebar: "tutorialSidebar"
              },
              {
                path: '/docs/prompts/ai-worker-prompts',
                component: ComponentCreator('/docs/prompts/ai-worker-prompts', '2b3'),
                exact: true,
                sidebar: "tutorialSidebar"
              },
              {
                path: '/docs/scripts/file-roles',
                component: ComponentCreator('/docs/scripts/file-roles', 'aa9'),
                exact: true,
                sidebar: "tutorialSidebar"
              },
              {
                path: '/docs/specifications/database-schema',
                component: ComponentCreator('/docs/specifications/database-schema', '614'),
                exact: true,
                sidebar: "tutorialSidebar"
              },
              {
                path: '/docs/specifications/dod-criteria',
                component: ComponentCreator('/docs/specifications/dod-criteria', '832'),
                exact: true,
                sidebar: "tutorialSidebar"
              },
              {
                path: '/docs/specifications/flow-details',
                component: ComponentCreator('/docs/specifications/flow-details', 'a1b'),
                exact: true,
                sidebar: "tutorialSidebar"
              },
              {
                path: '/docs/specifications/process-guide',
                component: ComponentCreator('/docs/specifications/process-guide', '6df'),
                exact: true,
                sidebar: "tutorialSidebar"
              },
              {
                path: '/docs/specifications/spreadsheet',
                component: ComponentCreator('/docs/specifications/spreadsheet', 'aa3'),
                exact: true,
                sidebar: "tutorialSidebar"
              },
              {
                path: '/docs/workflows/actual-processing-flow',
                component: ComponentCreator('/docs/workflows/actual-processing-flow', '316'),
                exact: true,
                sidebar: "tutorialSidebar"
              },
              {
                path: '/docs/workflows/business-flow',
                component: ComponentCreator('/docs/workflows/business-flow', '1e4'),
                exact: true,
                sidebar: "tutorialSidebar"
              },
              {
                path: '/docs/workflows/execution-checklist',
                component: ComponentCreator('/docs/workflows/execution-checklist', '82d'),
                exact: true,
                sidebar: "tutorialSidebar"
              },
              {
                path: '/docs/workflows/execution-modes',
                component: ComponentCreator('/docs/workflows/execution-modes', '6a2'),
                exact: true,
                sidebar: "tutorialSidebar"
              },
              {
                path: '/docs/workflows/overview',
                component: ComponentCreator('/docs/workflows/overview', '632'),
                exact: true,
                sidebar: "tutorialSidebar"
              },
              {
                path: '/docs/workflows/process-flow',
                component: ComponentCreator('/docs/workflows/process-flow', '14d'),
                exact: true,
                sidebar: "tutorialSidebar"
              }
            ]
          }
        ]
      }
    ]
  },
  {
    path: '/',
    component: ComponentCreator('/', 'e5f'),
    exact: true
  },
  {
    path: '*',
    component: ComponentCreator('*'),
  },
];
