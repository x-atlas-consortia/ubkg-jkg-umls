#!/usr/bin/env python
# coding: utf-8
"""
Class that builds a JSON that conforms to the
JSON Knowledge Graph (JKG) schema.
"""

import os
import gc
from typing import Any

import polars as pl
from tqdm import tqdm

# Configuration file class
from classes.ubkg_config import UbkgConfigParser
# Centralized logging class
from classes.ubkg_logging import UbkgLogging
# Class that reads and prepares data from UMLS flat files
from classes.umls_reader import UmlsReader
# Class that writes to JSON output
from classes.json_writer import JsonWriter
# Timer for Polars lazy event processing
from classes.ubkg_timer import UbkgTimer
from classes.refseq import Refseqapi

from classes.ubkg_extract import ubkgExtract

class JkgWriter:

    def __init__(self, cfg:UbkgConfigParser, ulog:UbkgLogging):

        self.cfg = cfg
        self.ulog = ulog
        self.uext = ubkgExtract(ulog=ulog)

        # Read configuration file to obtain location of output directory.
        self.output_dir = self.cfg.get_value(section='directories', key='output_dir')

        # Make output directory if it does not yet exist.
        os.system(f"mkdir -p {self.output_dir}")

        # HGNC gene definitions from RefSeq
        refseqapi = Refseqapi(ulog=ulog, cfg=cfg, uext=self.uext)
        self.gene_summaries = refseqapi.getrefseqsummaries(outdir=self.output_dir,
                                                      start=1,
                                                      chunk=200000)


        # JsonWriter object
        outfile = self.cfg.get_value(section='json_out', key='output_filename')
        outpath = os.path.join(self.output_dir, outfile)
        self.ulog.print_and_logger_info(f'Output file: {outpath}')
        pretty = self.cfg.get_value(section='json_out', key='pretty')
        indent = self.cfg.get_value(section='json_out', key='indent')
        self.json_writer = JsonWriter(outpath=outpath, pretty=pretty, indent=indent)

        """
        UMLS reader object
        During its instantiation, the UmlsReader object will read
        UMLS source files to build common DataFrames used
        to construct both nodes and rels lists. 
        JkgWriter will use the UmlsReader to read other UMLS files
        when they are needed.
        """

        self.ureader = UmlsReader(cfg=cfg, ulog=ulog)

        self._reformat_concept_code_rels_for_neo4j()

        # Start
        self.json_writer.start_json()

        # Build and write the nodes list.
        self._write_nodes_list()

        # Comma
        self.json_writer.write_comma()
        self.json_writer.write_line_feed()

        # Build and write the rels list.
        self._write_rels_list()

        # End
        self.json_writer.end_json()

    def _reformat_concept_code_rels_for_neo4j(self):
        """
        Reformats fields in the DataFrames of concept-code relationships
        and concept-concept relationships to JKG-compatible formats.

        Allowed patterns taken from the JKG Schema.
        Disallowed patterns derived from allowed patterns.
        """

        # nodes array -> properties -> rel_label
        self._REL_LABEL_PATTERN = r"^([a-z][a-z0-9_]*|CODE)$"
        self._REL_LABEL_DISALLOWED_CHARS = r"[^A-Za-z0-9_]"

        # rels array -> properties -> codeid
        self._CODE_ID_PATTERN = r"^[A-Za-z0-9._-]+:[A-Za-z0-9._-]+$"
        self._CODE_ID_DISALLOWED_CHARS = r"[^A-Za-z0-9._:-]"

        # Format relationship labels for neo4j compatibility.
        self.ureader.df_concept_concept_rels = self.ureader.df_concept_concept_rels.with_columns(
            self._reformat_field_for_neo4j(expr=pl.col("rel_label"), pattern=self._REL_LABEL_PATTERN).alias(
                "rel_label"))

        # Format codeid for neo4j compatibility and case for SAB.
        self.ureader.df_concept_code_rels = self.ureader.df_concept_code_rels.with_columns(
            self._reformat_field_for_neo4j(expr=pl.col("codeid"), pattern=self._CODE_ID_PATTERN).alias("codeid"))

    def _reformat_field_for_neo4j(self, expr: pl.Expr, pattern: str) -> pl.Expr:
        """
        Converts a string to a neo4j-compatible format.
        Used for relationship labels and codeids.

        :param expr: Polars expression for a string field.
        :param pattern: Neo4j-compatible pattern string.
        :return: Polars expression with transformed field.

        Converts a string to a neo4j-compatible format for a supported schema pattern.

        Behavior based on pattern:

        if self._REL_LABEL_PATTERN:
          - replace disallowed chars (self._REL_LABEL_DISALLOWED_CHARS)
            with "_"
          - preserve exact "CODE"
          - lowercase all other values
          - prepend "rel_" if result starts with a digit

        if self._CODE_ID_PATTERN:
          - replace disallowed chars (self._CODE_ID_DISALLOWED_CHARS)
            with "_"
          - ensure exactly one colon
          - uppercase the SAB portion before the colon
        """
        expr = expr.cast(pl.Utf8)

        if pattern == self._REL_LABEL_PATTERN:
            ret = expr.str.replace_all(self._REL_LABEL_DISALLOWED_CHARS, "_")

            ret = (
                pl.when(ret == "CODE")
                .then(pl.lit("CODE"))
                .otherwise(ret.str.to_lowercase())
            )

            ret = (
                pl.when(ret.str.slice(0, 1).str.contains(r"\d"))
                .then(pl.lit("rel_") + ret)
                .otherwise(ret)
            )

            return ret

        if pattern == self._CODE_ID_PATTERN:
            # Replace invalid characters, but keep ":" for now.
            ret = expr.str.replace_all(self._CODE_ID_DISALLOWED_CHARS, "_")

            # Split on colon.
            parts = ret.str.split(":")

            # First token becomes SAB.
            sab = parts.list.get(0).fill_null("").str.to_uppercase()

            # Remaining tokens become CODE, rejoined with underscores
            # so the final result has exactly one colon.
            code = (
                parts.list.slice(1)
                .list.join("_")
                .fill_null("")
            )

            return sab + pl.lit(":") + code

        raise ValueError(f"Unsupported pattern: {pattern}")

    def _unload_item(self, item_to_unload: Any):
        """
        Explicitly unloads an object from memory.
        :param item_to_unload: object to be unloaded

        """
        if type(item_to_unload) is list:
            item_to_unload.clear()
        if type(item_to_unload) is pl.DataFrame:
            item_to_unload = None

        gc.collect()

    def _get_progress_label(self, label_key:str) -> str:
        """
        Labels used in progress indicators, obtained from configuration.

        """
        label = self.cfg.get_value(section='progress_labels', key=label_key)

        if 'node' in label_key:
            return f'{label} nodes'
        else:
            return f'{label} rels'

    def _write_nodes_list(self):
        """
        Builds and writes the nodes list of the JKG file.

        """
        self.json_writer.start_list(keyname="nodes")

        # Source nodes
        list_nodes = self._get_source_node_list()
        list_name=self._get_progress_label("node_source")
        self.json_writer.write_list(list_name=list_name, list_content=list_nodes)
        if len(list_nodes) > 0:
            self.json_writer.write_comma()
            self.json_writer.write_line_feed()

        # Semantic Network Rel_Label nodes
        list_nodes = self._get_semantic_node_label_list()
        list_name = self._get_progress_label("node_semantic_rel")
        self.json_writer.write_list(list_name=list_name, list_content=list_nodes)
        if len(list_nodes) > 0:
            self.json_writer.write_comma()
            self.json_writer.write_line_feed()

        # Rel_Label nodes
        list_nodes = self._get_rel_label_list()
        list_name = self._get_progress_label("node_rel")
        self.json_writer.write_list(list_name=list_name, list_content=list_nodes)
        if len(list_nodes) > 0:
            self.json_writer.write_comma()
            self.json_writer.write_line_feed()

        # Concept nodes
        list_nodes = self._get_concept_nodes_list()
        list_name = self._get_progress_label("node_concept")
        self.json_writer.write_list(list_name=list_name, list_content=list_nodes)
        if len(list_nodes) > 0:
            self.json_writer.write_comma()
            self.json_writer.write_line_feed()

        # Term nodes
        list_nodes = self._get_term_nodes_list()
        list_name = self._get_progress_label("node_term")
        self.json_writer.write_list(list_name=list_name, list_content=list_nodes)

        self.json_writer.end_list()

    def _get_source_node_list(self) -> list:
        """
        Builds the list of Source nodes for the nodes array of the JKG.JSON.
        """

        # Obtain sorted current English-language SABs from MRSAB.RRF.
        colsabs = ['VSAB', 'RSAB', 'SON', 'SRL', 'TTYL','SVER']
        # VSAB - versioned source
        # RSAB - root source
        # SON - official name of source
        # SRL - source restriction level - can be used to filter out a licensed SAB.
        #       (https://uts.nlm.nih.gov/uts/license/license-category-help.html)
        # TTYL - term types from the SAB
        # SVER - source-version
        df = self.ureader.get_umls_file(filename='MRSAB', cols=colsabs)
        df = df.sort('RSAB')

        # Convert to a list of dictionaries for row-wise processing
        rows = df.to_dicts()

        # Unload DataFrame.
        self._unload_item(item_to_unload=df)

        # Build JSON output row by row.
        # Start with hard-coded rows for:
        # - JKG
        # - the UMLS itself
        # - NDC
        umls_release = self.ureader.get_umls_version()
        listsources = [
            {
                "labels": ["Source"],
                "properties": {
                    "id": "JKG:JKG",
                    "name": "JSON Knowledge Graph",
                    "description": "JSON specification for working with general knowledge graphs--specifically, property graphs.",
                    "sab": "JKG",
                    "source":"https://github.com/x-atlas-consortia/json-knowledge-graph"
                }
            },
            {
                "labels": ["Source"],
                "properties": {
                    "id": "UMLS:UMLS",
                    "name": "Unified Medical Language System",
                    "description": "United States National Institutes of Health (NIH) National Library of Medicine (NLM) Unified Medical Language System (UMLS) Knowledge Sources.",
                    "sab": "UMLS",
                    "source": "http://www.nlm.nih.gov/research/umls/licensedcontent/umlsknowledgesources.html"
                    },
                "source_version": umls_release
            },
            {
                "labels": ["Source"],
                "properties": {
                    "id": "UMLS:NDC",
                    "name": "National Drug Codes",
                    "sab": "NDC"
                }
            }
        ]

        desc = self._get_progress_label("node_source")
        for row in tqdm(rows, desc=f'Building {desc}'):
            dict_node = {
                "labels": ["Source"],
                "properties": {
                    "id": f"UMLS:{row['VSAB']}",
                    "name": row["SON"],
                    "sab": row["RSAB"],
                    "source_version": row["SVER"],
                    "srl": row["SRL"],
                    "ttyl": row["TTYL"].split(",") if row["TTYL"] else []  # Convert TTYL to a list or an empty list
                }
            }
            listsources.append(dict_node)

        return listsources

    def _get_semantic_node_label_list(self) -> list:
        """
        Builds the list of Node_Label nodes corresponding to the
        Semantic Network for the nodes array of the JKG.JSON.

        """

        list_nodes = []

        # Obtain the common semantic definitions dataset built by the
        # UmlsReader object at its initialization.
        df = self.ureader.df_semantic_definitions
        #df = df.filter(pl.col('RT') == 'STY')

        # Convert to a list of dictionaries for row-wise processing
        rows = df.to_dicts()
        self._unload_item(item_to_unload=df)

        # Build JSON output row by row.
        desc = self._get_progress_label("node_semantic_rel")
        for row in tqdm(rows, desc=f'Building {desc}'):
            dict_node = {
                "labels": ["Node_Label"],
                "properties": {
                    "id": f"UMLS:{row['UI']}",
                    "def": row["DEF"],
                    "node_label": row["STY_RL"],
                    "sab": "UMLS"}
            }
            list_nodes.append(dict_node)

        return list_nodes

    def _get_rel_label_list(self) -> list:
        """
        Builds the list of Rel_Label nodes for the nodes array of the JKG.JSON.

        """
        list_nodes = []

        #Get the DataFrame of concept-concept relationships built by the UmlsReader object.
        df = self.ureader.df_concept_concept_rels
        # Select the relationship labels.
        df = df.select('rel_label').unique().sort('rel_label')


        # Convert the columnar Polars DataFrame to dicts for row-level processing.
        rows = df.to_dicts()
        self._unload_item(item_to_unload=df)

        # Manually add the Rel_Label for CODE--i.e., coderels
        dict_node = {
            "labels": ["Rel_Label"],
            "properties": {
                "id": "JKG:CODE",
                "def": "contains information on a code in JKG--i.e., a representation of a Concept in a SAB",
                "rel_label": "CODE",
                "sab": "JKG"
            }
        }
        list_nodes.append(dict_node)

        desc = self._get_progress_label("node_rel")
        for row in tqdm(rows, desc=f'Building {desc}'):
            dict_node = {
                "labels": ["Rel_Label"],
                "properties": {
                    "id": f"UMLS:{row["rel_label"]}",
                    "def": row["rel_label"],
                    "rel_label": row["rel_label"],
                    "sab": "UMLS"}
            }
            list_nodes.append(dict_node)

        return list_nodes

    def _get_concept_labels_list(self) -> pl.DataFrame:
        """
        Obtains a DataFrame that aggregates the semantic types for each CUI.

        """

        # Get semantic atoms (terms) by concept.
        colsem = ['CUI', 'STY']
        # CUI
        # STY - semantic type
        df = self.ureader.get_umls_file(filename='MRSTY', cols=colsem)

        # normalize empty STY to null so they don't become empty strings in the lists
        df = df.with_columns(
            pl.when(pl.col("STY") == "").then(None).otherwise(pl.col("STY")).alias("STY")
        )

        # Group semantic type labels by CUI; aggregate into lists; then append "Concept" to each list.
        dfsty = (
            df.group_by("CUI", maintain_order=True)
            .agg(pl.col("STY"))
            .with_columns((pl.concat_list(pl.lit("Concept"), "STY")).alias("labels"))
        ).unique()

        return dfsty

    def _get_concept_nodes_list(self) -> list:
        """
        Builds the list of Concept nodes of the nodes array of the JKG.JSON.

        The "preferred term" of a concept in MRCONSO is defined as having the
        following characteristics:
        1. The Atom Status (TS) is preferred  (ISPREF=Y).
        2. The String type (STT) is "PF" (Preferred form of term).
        3. The Term Status is "P" (Preferred LUI of the CUI).

        However, many concepts in MRCONSO do not have preferred term
        rows, especially from the CHV and MSH SABs. For these concepts,
        the preferred term will be the blank string.

        """
        list_nodes = []

        # Obtain the subset of English-language, non-suppressed records from
        # MRCONSO built by the UmlsReader object.
        df = self.ureader.df_concept_code_rels

        # One row per concept from the full dataset, regardless of whether
        # the concept has a row that corresponds to a preferred term.
        concepts = df.select("CUI").unique()

        # Identify those concepts that have preferred terms per the criteria.
        preferred = (
            df.filter(pl.col("ISPREF") == "Y")
            .filter(pl.col("STT") == "PF")
            .filter(pl.col("TS") == "P")
            .select("CUI", pl.col("STR").alias("pref_term"))
            .unique(subset="CUI")
        )

        self._unload_item(item_to_unload=df)

        # Obtain sorted list of concept labels for each concept.
        dflabels = self._get_concept_labels_list()

        """
        For each concept, 
        1. Obtain the corresponding concept labels
        2. Obtain either the preferred term or the blank string.
        """
        concept_nodes_df = (
            concepts
            .join(dflabels, on="CUI", how="inner")
            .join(preferred, on="CUI", how="left")
            .with_columns(
                pl.col("pref_term").fill_null(""),
                pl.col("CUI").alias("id"),
                pl.lit("UMLS").alias("sab"),
            )
            .select("labels", "id", "pref_term", "sab")
            .unique()
        )


        # Unload DataFrames.
        self._unload_item(item_to_unload=preferred)
        self._unload_item(item_to_unload=dflabels)

        rows = concept_nodes_df.to_dicts()
        # Unload DataFrame.
        self._unload_item(item_to_unload=concept_nodes_df)

        desc = self._get_progress_label("node_concept")
        for row in tqdm(rows, desc=f'Building {desc}'):
           dict_node = {
               "labels": row["labels"],
               "properties": {
                   "id": f"UMLS:{row['id']}",
                   "pref_term": row["pref_term"],
                   "sab": "UMLS"
               }
           }
           list_nodes.append(dict_node)

        return list_nodes

    def _is_special_case_nan(self,cui:str) -> bool:
        """
        Identifies cases in which the term "NaN" corresponds to
        something other than np.NaN--e.g., the SCN11A/NaN protein.

        :param cui: CUI to check
        """
        return cui in [
            "C1419854", # SCN11A/NaN
            "C5958837" # SCN11A wt allele

        ]

    def _get_term_nodes_list(self) -> list:
        """
        Builds the list of Term nodes of the nodes array of the JKG.JSON.
        """
        list_nodes = []
        # For tracking unique values of STR.
        seen_terms = set()

        # Obtain the subset of English-language, non-suppressed records of
        # concept-code relationships built by the UmlsReader object.
        df = self.ureader.df_concept_code_rels.select(['CUI','STR']).unique().sort('STR')

        rows = df.to_dicts()
        # Unload DataFrame.
        self._unload_item(item_to_unload=df)

        desc = self._get_progress_label("node_term")
        for row in tqdm(rows, desc=f'Building {desc}'):
            """
            Distinguish between truly null terms (with the string "NaN" 
            as value) from terms that contain the string "NaN" 
            but are not null. Null terms are possible in MRCONSO.
            """
            term = row["STR"]
            if term is not None:
                if term == "NaN":
                    if self._is_special_case_nan(cui=row['CUI']):
                        term = "NaN (term)"
                    else:
                        continue

                    """
                    Deduplicate on the final term value, since unique() above
                    only guaranteed uniqueness of the (CUI, STR) pair, not STR
                    (or the post-substitution term) alone.
                    """
                    if term in seen_terms:
                        continue
                    seen_terms.add(term)

                dict_node = {
                    "labels": ["Term"],
                    "properties": {
                        "id": term
                        }
                }
                list_nodes.append(dict_node)

        return list_nodes

    def _write_rels_list(self):
        """
        Builds and writes the rels array of the JKG.JSON.

        """
        # Build rels array:
        # 1. Semantic relationships
        # 2. Concept-concept relationships
        # 3. Concept-code relationships
        # 4. Add maps of NDC codes to CUIs to rels

        self.json_writer.start_list(keyname="rels")

        # Semantic Network rels
        list_rels = self._get_semantic_rel_list()
        list_name = self._get_progress_label("rel_semantic")
        self.json_writer.write_list(list_name=list_name, list_content=list_rels)
        if len(list_rels) > 0:
            self.json_writer.write_comma()
            self.json_writer.write_line_feed()

        # Concept-concept rels
        list_rels = self._get_concept_concept_rel_list()
        list_name = self._get_progress_label("rel_concept_concept")
        self.json_writer.write_list(list_name=list_name, list_content=list_rels)
        if len(list_rels) > 0:
            self.json_writer.write_comma()
            self.json_writer.write_line_feed()

        # Concept-code rels
        list_rels = self._get_concept_code_rel_list()
        list_name = self._get_progress_label("rel_concept_code")
        self.json_writer.write_list(list_name=list_name, list_content=list_rels)

        # NDC code rels
        list_rels = self._get_ndc_code_rel_list()
        list_name = self._get_progress_label("rel_ndc")
        if len(list_rels) > 0:
            self.json_writer.write_comma()
            self.json_writer.write_line_feed()
            self.json_writer.write_list(list_name=list_name, list_content=list_rels)

        self.json_writer.end_list()

    def _get_semantic_rel_list(self) -> list:
        """
        Builds the list of semantic rels array of the JKG.JSON.
        """
        list_rels = []

        # Obtain the common semantic definitions dataset built by the
        # UmlsReader object at its initialization.
        df = self.ureader.df_semantic_definitions

        # Obtain from SRSTRE1 (Fully inherited set of Relations (UI's),
        # a file of the Semantic Network.
        # Filter to the basic hierarchical relationships (T186).
        df_srs = (self.ureader.get_umls_file(filename='SRSTRE1')
                  .filter(pl.col('UI2')=='T186'))

        # Join semantic definition DataFrame with the relations DataFrame
        # to obtain isa relationships between elements of the Semantic
        # Network.
        df = df.join(df_srs,
                     how='inner',
                     left_on='UI',
                     right_on='UI1',
                     maintain_order='left').sort(['UI','UI3'])

        rows = df.to_dicts()

        # Unload DataFrames.
        self._unload_item(item_to_unload=df)
        self._unload_item(item_to_unload=df_srs)

        # At this point, the stored DataFrame of semantic definitions is no longer needed.
        self._unload_item(item_to_unload=self.ureader.df_semantic_definitions)

        desc = self._get_progress_label("rel_semantic")
        for row in tqdm(rows, desc=f'Building {desc}'):
            dict_rel = {
                "label": "isa",
                "end": {
                    "properties" : {
                        "id": f"UMLS:{row['UI3']}"
                    }
                },
                "properties":{
                            "sab":"UMLS"
                },
                "start": {
                    "properties" : {
                        "id": f"UMLS:{row['UI']}"
                    }
                }
            }
            list_rels.append(dict_rel)

        return list_rels

    def _get_concept_concept_rel_list(self) -> list:
        """
        Builds the list of concept-concept rels array of the JKG.JSON.
        """
        list_rels = []

        # Obtain the common concept-concept relationship dataset built by the
        # UmlsReader object at its initialization.
        df_rels = self.ureader.df_concept_concept_rels

        """
        Filter out concept-concept relationships involving concepts that are not 
        in df_concept_code_rels--i.e., concepts that are obsolete or suppressed.
        """
        # Concept-code relationships used to determine which CUIs are present.
        df_code_rels = self.ureader.df_concept_code_rels

        valid_cuis = df_code_rels.select("CUI").unique()

        # Join twice against the frame of unique CUIs--once for starting concepts,
        # and once for ending concepts.
        df_rels = df_rels.join(
            valid_cuis.rename({"CUI": "CUI1"}),
            on="CUI1",
            how="inner",
        ).join(
            valid_cuis.rename({"CUI": "CUI2"}),
            on="CUI2",
            how="inner",
        )

        rows = df_rels.to_dicts()

        # Unload DataFrame.
        self._unload_item(item_to_unload=df_rels)
        self._unload_item(item_to_unload=df_code_rels)
        self._unload_item(item_to_unload=valid_cuis)

        # At this point, the stored DataFrame of concept-concept rels
        # is no longer needed.
        self._unload_item(item_to_unload=self.ureader.df_concept_code_rels)

        desc = self._get_progress_label("rel_concept_concept")
        for row in tqdm(rows, desc=f'Building {desc}'):

            # In the concept-concept relationship DataFrame,
            # CUI2 identifies the start concept and CUI1 identifies
            # the end concept of the relationship.
            dict_rel = {
                "label": f"{row['rel_label']}",
                "end": {
                    "properties" : {
                        "id": f"UMLS:{row['CUI1']}"
                    }
                },
                "properties":{
                            "sab": row["SAB"]
                },
                "start": {
                    "properties" : {
                        "id": f"UMLS:{row['CUI2']}"
                    }
                }
            }

            list_rels.append(dict_rel)

        return list_rels


    def _get_concept_code_rel_list(self) -> list:
        """
        Builds the list of concept-code rels array of the JKG.JSON.
        """
        list_rels = []
        refseqapi = Refseqapi(ulog=self.ulog, cfg=self.cfg, uext=self.uext)

        # Obtain the common concept-code relationship dataset built by the
        # UmlsReader object at its initialization.

        # Build HGNC_ID -> definition lookup dict once before the loop
        #hgnc_def_map: dict = self.gene_summaries.set_index('HGNC_ID')['definition'].to_dict()
        hgnc_def_map: dict = {f"HGNC:{k}": v for k, v in
                              self.gene_summaries.set_index('Entrez')['definition'].to_dict().items()}

        # Obtain the common concept-code relationship dataset built by the
        # UmlsReader object at its initialization.

        rows = self.ureader.df_concept_code_rels.to_dicts()

        desc = self._get_progress_label("rel_concept_code")
        for row in tqdm(rows, desc=f'Building {desc}'):

            dict_rel = {
                "label": "isa",
                "end": {}
            }
            if row["SAB"] == "HGNC":
                row["DEF"] = hgnc_def_map.get(row["codeid"], "")

            endid = row["STR"]
            # Filter out truly null concept-code maps, accounting
            # for special cases such as SCNA11A.
            if endid is not None:

                if endid == "NaN":
                    if self._is_special_case_nan(cui=row["CUI"]):
                        endid = "NaN (term)"
                    else:
                        continue

                dict_rel = {
                    "label": "CODE",
                    "end": {
                        "properties": {
                            "id": endid
                        }
                    },
                    "properties": {
                        "sab": row["SAB"],
                        "def": row["DEF"],
                        "tty": row["TTY"],
                        "codeid": row["codeid"]
                    },
                    "start": {
                        "properties": {
                            "id": f"UMLS:{row['CUI']}"
                        }
                    }
                }

                list_rels.append(dict_rel)

        return list_rels

    def _get_ndc_code_rel_list(self) -> list:
        """
        Builds the list of CODE rel nodes for generic NDC codes in the JKG.JSON.

        The NDC vocabulary is not structured as a separate vocabulary
        in the UMLS; instead, NDC codes are attributes of codes in
        RXNORM. (The RXNav application shows the relationships between
        NDC packaging codes and RXNORM codes.)

        This function converts the attribute mapping into sets of
        NDC codes that share RXNORM CUIs, limiting to generic NDC codes.

        """

        self.ulog.print_and_logger_info('Building NDC concept-code relationships')
        listrels = []

        # Obtain non-suppressed attribute values from MRSAT.
        colsat = ['CODE', # code in vocabulary
                   'SAB', # source vocbulary (RSAB or VSAB)
                   'ATV', # attribute value
                   'ATN' # attribute name
                  ]

        # MRSAT contains strings with double quotes, so use a cleaned version.
        df = self.ureader.get_umls_file(filename='MRSAT', cols=colsat, clean_file=True)

        utimer = UbkgTimer(display_msg="Processing for NDC codes")

        # Filter to NDC attributes of RXNORM codes.
        df = (df.filter(pl.col('SAB') == 'RXNORM')
              .filter(pl.col('ATN') == 'NDC'))

        df = df.with_columns((pl.col('SAB')+':'+pl.col('CODE')).alias('codeid'))
        df = df.with_columns((pl.lit('NDC:') + pl.col('ATV')).alias('ndcid'))

        # Join against the DataFrame of concept-code relationships to
        # get information on NDC concepts.
        # Filter out term types of:
        # SY - synonym
        # PSN - prescribable names
        # TMSY - Tall Man synonym
        df = ((self.ureader.df_concept_code_rels.join(
            df,
            how = 'inner',
            on='codeid'
        ).filter(pl.col('TTY') != 'SY')
               .filter(pl.col('TTY') != 'PSN'))
              .filter(pl.col('TTY') != 'TMSY'))

        utimer.stop()

        rows = df.to_dicts()

        # Unload DataFrame.
        self._unload_item(item_to_unload=df)

        # At this point, the stored DataFrame of concept-code relationships
        # is no longer needed.
        self._unload_item(item_to_unload=self.ureader.df_concept_code_rels)

        desc = self._get_progress_label("rel_ndc")
        for row in tqdm(rows, desc=f'Building {desc}'):
            # Filter out null terms from input.
            if row['STR'] is None or row['STR']=='NaN':
                continue

            dict_rel = {
                "label": "CODE",
                "end": {
                    "properties": {
                        "id": row["STR"]
                    }
                },
                "properties": {
                    "sab": "NDC",
                    "tty": row["TTY"],
                    "codeid": row["codeid"]
                },
                "start": {
                    "properties": {
                        "id": f"UMLS:{row['CUI']}"
                    }
                }
            }
            listrels.append(dict_rel)

        return listrels



