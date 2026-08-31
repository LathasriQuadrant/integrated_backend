# """
# Tableau Metadata API (GraphQL) client.

# The Metadata API is the primary source for lineage, data-model
# relationships (logical/physical tables, joins), calculated field
# formulas, and upstream/downstream dependency chains.
# """

# from __future__ import annotations

# from typing import Any

# import httpx

# from app.auth.session import TableauSession
# from app.config import get_settings


# WORKBOOK_GRAPHQL_QUERY = """
# query WorkbookDiscovery($workbookLuid: String!) {
#   workbooks(filter: { luid: $workbookLuid }) {
#     luid
#     name
#     description
#     projectName
#     owner { name username }
#     createdAt
#     updatedAt

#     dashboards {
#       luid
#       name
#     }

#     sheets {
#       luid
#       name
#       containedInDashboards { name }
#     }

#     upstreamDatasources {
#       luid
#       name
#       hasExtracts
#       isCertified
#       fields {
#         name
#         __typename
#       }
#     }

#     embeddedDatasources {
#       name
#       hasExtracts
#       upstreamTables {
#         name
#         schema
#         database { name connectionType }
#       }
#       fields {
#         name
#         __typename
#         ... on CalculatedField {
#           formula
#           aggregation
#           dataType
#         }
#       }
#     }

#     upstreamTables {
#       name
#       schema
#       database { name connectionType }
#     }
#   }
# }
# """

# DATASOURCE_GRAPHQL_QUERY = """
# query DatasourceDiscovery($datasourceLuid: String!) {
#   publishedDatasources(filter: { luid: $datasourceLuid }) {
#     luid
#     name
#     hasExtracts
#     isCertified
#     projectName
#     owner { name }

#     upstreamTables {
#       name
#       schema
#       database { name connectionType }
#     }

#     upstreamDatabases {
#       name
#       connectionType
#     }

#     fields {
#       name
#       __typename
#       ... on CalculatedField {
#         formula
#         aggregation
#         dataType
#       }
#       ... on ColumnField {
#         dataType
#         aggregation
#       }
#     }

#     downstreamWorkbooks {
#       luid
#       name
#     }

#     downstreamSheets {
#       luid
#       name
#     }
#   }
# }
# """

# LINEAGE_GRAPHQL_QUERY = """
# query LineageDiscovery($datasourceLuid: String!) {
#   publishedDatasources(filter: { luid: $datasourceLuid }) {
#     luid
#     name
#     upstreamTables {
#       name
#       schema
#       upstreamTables { name schema }
#       database { name connectionType }
#     }
#     downstreamWorkbooks {
#       luid
#       name
#       projectName
#     }
#     downstreamDashboards {
#       luid
#       name
#     }
#   }
# }
# """

# CUSTOM_SQL_GRAPHQL_QUERY = """
# query CustomSqlDiscovery($workbookLuid: String!) {
#   workbooks(filter: { luid: $workbookLuid }) {
#     luid
#     name
#     embeddedDatasources {
#       name
#       upstreamTables {
#         name
#       }
#     }
#     upstreamTables {
#       name
#       isCustomSql
#       query
#     }
#   }
# }
# """


# class TableauMetadataApiClient:
#     """Thin wrapper around the Tableau GraphQL Metadata API."""

#     def __init__(self, session: TableauSession):
#         self._session = session
#         self._settings = get_settings()
#         self._url = f"{self._settings.TABLEAU_SERVER}{self._settings.METADATA_API_PATH}"

#     async def _execute(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
#         headers = {
#             "X-Tableau-Auth": self._session.token,
#             "Content-Type": "application/json",
#             "Accept": "application/json",
#         }
#         payload = {"query": query, "variables": variables}

#         async with httpx.AsyncClient(timeout=self._settings.REQUEST_TIMEOUT_SECONDS) as client:
#             resp = await client.post(self._url, headers=headers, json=payload)
#             resp.raise_for_status()
#             body = resp.json()

#         if "errors" in body and body["errors"]:
#             # Metadata API returns 200 with an "errors" array on partial failure.
#             # We surface it via the caller rather than raising, so a single
#             # workbook's GraphQL issue doesn't abort the whole discovery run.
#             return {"data": body.get("data") or {}, "errors": body["errors"]}

#         return {"data": body.get("data", {}), "errors": []}

#     async def get_workbook_graph(self, workbook_luid: str) -> dict[str, Any]:
#         result = await self._execute(WORKBOOK_GRAPHQL_QUERY, {"workbookLuid": workbook_luid})
#         workbooks = result["data"].get("workbooks", [])
#         return workbooks[0] if workbooks else {}

#     async def get_datasource_graph(self, datasource_luid: str) -> dict[str, Any]:
#         result = await self._execute(DATASOURCE_GRAPHQL_QUERY, {"datasourceLuid": datasource_luid})
#         sources = result["data"].get("publishedDatasources", [])
#         return sources[0] if sources else {}

#     async def get_lineage(self, datasource_luid: str) -> dict[str, Any]:
#         result = await self._execute(LINEAGE_GRAPHQL_QUERY, {"datasourceLuid": datasource_luid})
#         sources = result["data"].get("publishedDatasources", [])
#         return sources[0] if sources else {}

#     async def get_custom_sql(self, workbook_luid: str) -> dict[str, Any]:
#         result = await self._execute(CUSTOM_SQL_GRAPHQL_QUERY, {"workbookLuid": workbook_luid})
#         workbooks = result["data"].get("workbooks", [])
#         return workbooks[0] if workbooks else {}
"""
Tableau Metadata API (GraphQL) client.

The Metadata API is the primary source for lineage, data-model
relationships (logical/physical tables, joins), calculated field
formulas, and upstream/downstream dependency chains.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.auth.session import TableauSession
from app.config import get_settings


WORKBOOK_GRAPHQL_QUERY = """
query WorkbookDiscovery($workbookLuid: String!) {
  workbooks(filter: { luid: $workbookLuid }) {
    luid
    name
    description
    projectName
    owner { name username }
    createdAt
    updatedAt

    dashboards {
      luid
      name
    }

    sheets {
      luid
      name
      containedInDashboards { name }
    }

    upstreamDatasources {
      luid
      name
      hasExtracts
      isCertified
      fields {
        name
        __typename
      }
    }

    embeddedDatasources {
      name
      hasExtracts
      upstreamTables {
        name
        schema
        database { name connectionType }
        columns {
          name
          remoteType
        }
      }
      fields {
        name
        __typename
        ... on CalculatedField {
          formula
          aggregation
          dataType
        }
      }
    }

    upstreamTables {
      name
      schema
      database { name connectionType }
      columns {
        name
        remoteType
      }
    }
  }
}
"""

DATASOURCE_GRAPHQL_QUERY = """
query DatasourceDiscovery($datasourceLuid: String!) {
  publishedDatasources(filter: { luid: $datasourceLuid }) {
    luid
    name
    hasExtracts
    isCertified
    projectName
    owner { name }

    upstreamTables {
      name
      schema
      database { name connectionType }
      columns {
        name
        remoteType
      }
    }

    upstreamDatabases {
      name
      connectionType
    }

    fields {
      name
      __typename
      ... on CalculatedField {
        formula
        aggregation
        dataType
      }
      ... on ColumnField {
        dataType
        aggregation
      }
    }

    downstreamWorkbooks {
      luid
      name
    }

    downstreamSheets {
      luid
      name
    }
  }
}
"""

LINEAGE_GRAPHQL_QUERY = """
query LineageDiscovery($datasourceLuid: String!) {
  publishedDatasources(filter: { luid: $datasourceLuid }) {
    luid
    name
    upstreamTables {
      name
      schema
      upstreamTables { name schema }
      database { name connectionType }
    }
    downstreamWorkbooks {
      luid
      name
      projectName
    }
    downstreamDashboards {
      luid
      name
    }
  }
}
"""

CUSTOM_SQL_GRAPHQL_QUERY = """
query CustomSqlDiscovery($workbookLuid: String!) {
  workbooks(filter: { luid: $workbookLuid }) {
    luid
    name
    embeddedDatasources {
      name
      upstreamTables {
        name
      }
    }
    upstreamTables {
      name
      isCustomSql
      query
    }
  }
}
"""


class TableauMetadataApiClient:
    """Thin wrapper around the Tableau GraphQL Metadata API."""

    def __init__(self, session: TableauSession):
        self._session = session
        self._settings = get_settings()
        self._url = f"{self._settings.TABLEAU_SERVER}{self._settings.METADATA_API_PATH}"

    async def _execute(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "X-Tableau-Auth": self._session.token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        payload = {"query": query, "variables": variables}

        async with httpx.AsyncClient(timeout=self._settings.REQUEST_TIMEOUT_SECONDS) as client:
            resp = await client.post(self._url, headers=headers, json=payload)
            resp.raise_for_status()
            body = resp.json()

        if "errors" in body and body["errors"]:
            # Metadata API returns 200 with an "errors" array on partial failure.
            # We surface it via the caller rather than raising, so a single
            # workbook's GraphQL issue doesn't abort the whole discovery run.
            return {"data": body.get("data") or {}, "errors": body["errors"]}

        return {"data": body.get("data", {}), "errors": []}

    async def get_workbook_graph(self, workbook_luid: str) -> dict[str, Any]:
        result = await self._execute(WORKBOOK_GRAPHQL_QUERY, {"workbookLuid": workbook_luid})
        workbooks = result["data"].get("workbooks", [])
        return workbooks[0] if workbooks else {}

    async def get_datasource_graph(self, datasource_luid: str) -> dict[str, Any]:
        result = await self._execute(DATASOURCE_GRAPHQL_QUERY, {"datasourceLuid": datasource_luid})
        sources = result["data"].get("publishedDatasources", [])
        return sources[0] if sources else {}

    async def get_lineage(self, datasource_luid: str) -> dict[str, Any]:
        result = await self._execute(LINEAGE_GRAPHQL_QUERY, {"datasourceLuid": datasource_luid})
        sources = result["data"].get("publishedDatasources", [])
        return sources[0] if sources else {}

    async def get_custom_sql(self, workbook_luid: str) -> dict[str, Any]:
        result = await self._execute(CUSTOM_SQL_GRAPHQL_QUERY, {"workbookLuid": workbook_luid})
        workbooks = result["data"].get("workbooks", [])
        return workbooks[0] if workbooks else {}
