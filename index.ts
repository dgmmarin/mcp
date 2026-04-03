#!/usr/bin/env node
import http from "node:http";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { createMcpExpressApp } from "@modelcontextprotocol/sdk/server/express.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";

// ─── Config ────────────────────────────────────────────────────────────────
const API_BASE_URL = process.env.API_BASE_URL || "http://localhost:3003";
const API_KEY      = process.env.API_KEY || "";
const MCP_PORT     = parseInt(process.env.MCP_PORT || "3333", 10);
const MCP_HOST     = process.env.MCP_HOST || "127.0.0.1";

async function apiFetch(path: string, params: Record<string, string> = {}) {
  const url = new URL(`${API_BASE_URL}${path}`);
  Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
  const res = await fetch(url.toString(), {
    headers: {
      Authorization: `Bearer ${API_KEY}`,
      "Content-Type": "application/json",
    },
  });
  if (!res.ok) throw new Error(`API error ${res.status}: ${await res.text()}`);
  return res.json();
}

// ─── MCP Server ────────────────────────────────────────────────────────────
function createServer(): Server {
  const server = new Server(
    { name: "bridges-inspections-mcp", version: "2.0.0" },
    { capabilities: { tools: {} } },
  );

  // ─── Tool Definitions ──────────────────────────────────────────────────
  server.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools: [
      {
        name: "get_bridge_statistics",
        description:
          "Returns aggregate counts of bridges grouped by their latest inspection status (pending, in_progress, completed). Optionally filter by tenant.",
        inputSchema: {
          type: "object",
          properties: {
            tenantUuid: {
              type: "string",
              description: "Optional: filter by tenant UUID",
            },
          },
          required: [],
        },
      },
      {
        name: "list_bridges",
        description:
          "Paginated list of bridges with optional filters. Supports filtering by name, inspection status, condition grade (A/B/C/D), inspector, company, fiscal year, and geography.",
        inputSchema: {
          type: "object",
          properties: {
            limit: {
              type: "string",
              description: "Page size (default 10, max 50)",
            },
            page: { type: "string", description: "Page number (1-based, default 1)" },
            uuid: { type: "string", description: "Filter by bridge UUID" },
            tenantUuid: { type: "string", description: "Filter by tenant UUID" },
            name: {
              type: "string",
              description: "Filter by bridge name (2–100 chars)",
            },
            latitude: {
              type: "string",
              description: "Filter by latitude (-90 to 90)",
            },
            longitude: {
              type: "string",
              description: "Filter by longitude (-180 to 180)",
            },
            inspectionStatuses: {
              type: "string",
              description:
                "Comma-separated inspection statuses to filter by: pending, in_progress, completed, in_review",
            },
            soundAssesment: {
              type: "string",
              description:
                "Comma-separated condition grades to filter by: A, B, C, D, -",
            },
            inspectionFiscalYears: {
              type: "string",
              description: "Comma-separated fiscal years (YYYY) to filter by",
            },
            inspectorUuids: {
              type: "string",
              description: "Comma-separated inspector UUIDs (max 25)",
            },
            companyUuids: {
              type: "string",
              description: "Comma-separated inspector company UUIDs (max 25)",
            },
          },
          required: [],
        },
      },
      {
        name: "get_bridge",
        description:
          "Get full details for a single bridge by UUID, including location, structural info, inspection status, condition grade, and inspector assignment.",
        inputSchema: {
          type: "object",
          properties: {
            uuid: { type: "string", description: "Bridge UUID" },
          },
          required: ["uuid"],
        },
      },
      {
        name: "get_bridge_gallery",
        description:
          "Get the photo gallery for a bridge (CAD drawings and general photos).",
        inputSchema: {
          type: "object",
          properties: {
            uuid: { type: "string", description: "Bridge UUID" },
          },
          required: ["uuid"],
        },
      },
      {
        name: "list_inspections",
        description:
          "Paginated list of inspections with optional filters. Supports filtering by bridge, inspector, reviewer, status, and tenant. Set withDetails=true to include evaluations, photos, and defects.",
        inputSchema: {
          type: "object",
          properties: {
            limit: {
              type: "string",
              description: "Page size (default 10, max 50)",
            },
            page: { type: "string", description: "Page number (1-based, default 1)" },
            bridgeUuid: { type: "string", description: "Filter by bridge UUID" },
            inspectorUuid: {
              type: "string",
              description: "Filter by inspector UUID",
            },
            reviewerUuid: {
              type: "string",
              description: "Filter by reviewer UUID",
            },
            inspectionUuid: {
              type: "string",
              description: "Filter by specific inspection UUID",
            },
            tenantUuid: { type: "string", description: "Filter by tenant UUID" },
            status: {
              type: "string",
              enum: ["pending", "in_progress", "completed", "in_review"],
              description: "Filter by inspection status",
            },
            withDetails: {
              type: "string",
              enum: ["true", "false"],
              description:
                "Include full sub-resources: evaluations, photos, defects (default: false)",
            },
          },
          required: [],
        },
      },
      {
        name: "get_inspection",
        description:
          "Get full details for a single inspection by UUID, including condition grade, defects, technical evaluations, photos, and reviewer report.",
        inputSchema: {
          type: "object",
          properties: {
            uuid: { type: "string", description: "Inspection UUID" },
          },
          required: ["uuid"],
        },
      },
      {
        name: "list_bridge_inspection_galleries",
        description:
          "Paginated list of inspection photo galleries for a specific bridge. Optionally filter by status or inspection UUID.",
        inputSchema: {
          type: "object",
          properties: {
            uuid: { type: "string", description: "Bridge UUID" },
            limit: { type: "string", description: "Max 2 per page" },
            page: { type: "string", description: "Page number (1-based, default 1)" },
            status: {
              type: "string",
              enum: ["pending", "in_progress", "completed", "in_review"],
              description: "Filter by inspection status",
            },
            inspectionUuid: {
              type: "string",
              description: "Filter by specific inspection UUID",
            },
          },
          required: ["uuid"],
        },
      },
    ],
  }));

  // ─── Tool Handlers ──────────────────────────────────────────────────────
  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const { name, arguments: args } = request.params;
    const a = (args ?? {}) as Record<string, string>;

    try {
      let data: unknown;

      switch (name) {
        case "get_bridge_statistics": {
          const params: Record<string, string> = {};
          if (a.tenantUuid) params.tenantUuid = a.tenantUuid;
          data = await apiFetch("/bridges/statistics", params);
          break;
        }

        case "list_bridges": {
          const params: Record<string, string> = {};
          const fields = [
            "limit",
            "page",
            "uuid",
            "tenantUuid",
            "name",
            "latitude",
            "longitude",
            "inspectionStatuses",
            "soundAssesment",
            "inspectionFiscalYears",
            "inspectorUuids",
            "companyUuids",
          ] as const;
          for (const f of fields) if (a[f]) params[f] = a[f];
          data = await apiFetch("/bridges", params);
          break;
        }

        case "get_bridge":
          data = await apiFetch(`/bridges/${a.uuid}`);
          break;

        case "get_bridge_gallery":
          data = await apiFetch(`/bridges/${a.uuid}/gallery`);
          break;

        case "list_inspections": {
          const params: Record<string, string> = {};
          const fields = [
            "limit",
            "page",
            "bridgeUuid",
            "inspectorUuid",
            "reviewerUuid",
            "inspectionUuid",
            "tenantUuid",
            "status",
            "withDetails",
          ] as const;
          for (const f of fields) if (a[f]) params[f] = a[f];
          data = await apiFetch("/inspections", params);
          break;
        }

        case "get_inspection":
          data = await apiFetch(`/inspections/${a.uuid}`);
          break;

        case "list_bridge_inspection_galleries": {
          const params: Record<string, string> = {};
          if (a.limit) params.limit = a.limit;
          if (a.page) params.page = a.page;
          if (a.status) params.status = a.status;
          if (a.inspectionUuid) params.inspectionUuid = a.inspectionUuid;
          data = await apiFetch(
            `/bridges/${a.uuid}/inspection-galleries`,
            params,
          );
          break;
        }

        default:
          return {
            content: [{ type: "text", text: `Unknown tool: ${name}` }],
            isError: true,
          };
      }

      return {
        content: [{ type: "text", text: JSON.stringify(data, null, 2) }],
      };
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      return {
        content: [{ type: "text", text: `Error: ${message}` }],
        isError: true,
      };
    }
  });

  return server;
}

// ─── HTTP entrypoint ───────────────────────────────────────────────────────
async function main() {
  // Stateless transport — a fresh transport + server per request keeps things
  // simple for a single-agent deployment. No session management needed.
  const app = createMcpExpressApp({ host: MCP_HOST });

  app.post("/mcp", async (req, res) => {
    const transport = new StreamableHTTPServerTransport({
      sessionIdGenerator: undefined, // stateless
    });
    const server = createServer();
    await server.connect(transport);
    await transport.handleRequest(req, res, req.body);
    await server.close();
  });

  app.get("/mcp", async (req, res) => {
    const transport = new StreamableHTTPServerTransport({
      sessionIdGenerator: undefined,
    });
    const server = createServer();
    await server.connect(transport);
    await transport.handleRequest(req, res);
    await server.close();
  });

  app.delete("/mcp", async (_req, res) => {
    res.status(405).json({ error: "Session management not supported in stateless mode" });
  });

  const httpServer = http.createServer(app);
  httpServer.listen(MCP_PORT, MCP_HOST, () => {
    console.error(
      `Bridges & Inspections MCP Server listening on http://${MCP_HOST}:${MCP_PORT}/mcp`,
    );
  });
}

main().catch(console.error);
