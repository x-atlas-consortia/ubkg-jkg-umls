#!/usr/bin/env python
# coding: utf-8
"""
Class that reads and translates data from the flat source files
of a subset produced by the UMLS MetamorphoSYS application.
"""

import os
import time
from datetime import timedelta

import polars as pl
from tqdm import tqdm

# Configuration file class
from classes.ubkg_config import UbkgConfigParser
# Centralized logging class
from classes.ubkg_logging import UbkgLogging
# Timer for Polars lazy event processing
from classes.ubkg_timer import UbkgTimer

# Functions to standardize codes and terms from vocabularies
from utilities.ubkg_standardize import create_codeid, standardize_codeid, standardize_term
# color printing
from utilities.print_color import print_color

class UmlsReader:

    def __init__(self, cfg: UbkgConfigParser, ulog: UbkgLogging):

        self.cfg = cfg
        self.ulog = ulog

        umls_dir = cfg.get_value(section='directories', key='umls_dir')
        if not os.path.exists(umls_dir):
            self.ulog.print_and_logger_info(f'UMLS source directory {umls_dir} not found')
            exit(1)

        # Build Dataframes that will be used to build elements in
        # both the nodes and the rels arrays in JKG.JSON.

        # Concept-concept relationships
        self.df_concept_concept_rels = self._get_concept_concept_rels()

        # Concept-code relationships
        self.df_concept_code_rels = self._get_concept_code_rels()

        # Semantic definitions
        self.df_semantic_definitions = self._get_semantic_definitions()

    def get_umls_file(self,filename: str, suppress: bool = True,
                      english: bool = True, curver: bool = True, n_rows=None, cols=None,
                      clean_file: bool = False) -> pl.DataFrame:
        """
        Returns a DataFrame corresponding to the optionally filtered content of a UMLS file.
        Uses lazy loading (pl.scan_csv and collect).

        :param suppress: if True and the file has a SUPPRESS (Suppressible) column, suppress
        :param english: if True and the file has a LAT (Language) column, filter to English
        :param curver: if True the file has a CURVER (Current Version) column, filter to current version
        :param filename: UMLS filename
        :param n_rows: number of rows to read
        :param cols: columns to return
        :param clean_file: if True the file will be pre-processed to remove double-quoted strings
        """

        # Check configuration for a non-zero number of rows to fetch
        # for uses such as debugging.
        debug_n_rows = int(self.cfg.get_value(section='debug', key='debug_n_rows'))
        if debug_n_rows > 0:
            msg = f'DEBUG: Reading only {debug_n_rows} rows from {filename}.'
            print_color(message=msg, colorcode='red')
            n_rows = debug_n_rows

        # The Semantic Network definitions files and Metathesaurus files
        # are located in separate directories.
        if filename[:2] == 'SR':
            ufile = os.path.join(self.cfg.get_value(section='directories', key='umls_dir'), 'NET', filename)
        else:
            ufile = os.path.join(self.cfg.get_value(section='directories', key='umls_dir'), 'META', filename + '.RRF')

        # Because scan_csv works with the entire file at once,
        # It does not report the number of rows in the file.
        # Provide an estimate to document performance.
        if n_rows is not None:
            # Explicit subset.
            est_total = n_rows
        else:
            # Estimate, using the known file size in bytes and the number
            # of characters in a sample row in the file.
            file_size = os.path.getsize(ufile)
            # Obtain the sample row size for the file from configuration.
            avg_row_size = int(self.cfg.get_value(section='rowsizes', key=filename))
            est_total = int(round(file_size / avg_row_size, 0))

        rownum = str(est_total)

        # Obtain the column header names from configuration.
        listcol = self.cfg.get_value(section='columns', key=filename).split(',')
        if cols is None:
            cols = listcol

        # Determine optional filtering.
        checksuppress = suppress and 'SUPPRESS' in listcol
        checkenglish = english and 'LAT' in listcol
        checkcurver = curver and 'CURVER' in listcol

        if clean_file:
            # Pre-process (clean) the source file if one does not already exist.
            ufile = self._get_clean_file(filename=filename)

        # If the file is to be scanned instead of read, provide the historical
        # scan/collection time.
        scan_estimate = self._estimate_scan_time(filename=filename)
        if scan_estimate != '':
            print_color(message=scan_estimate, colorcode='green')

        # Scan--i.e., use lazy loading and filtering.
        try:
            start_time = time.time()

            df = self._scan_with_timer(filename=ufile,
                                       separator='|',
                                       new_columns=listcol,
                                       n_rows=n_rows,
                                       checksuppress=checksuppress,
                                       checkenglish=checkenglish,
                                       checkcurver=checkcurver)
            if cols is not None:
                df = df.select(cols)

            end_time = time.time()
            duration = end_time - start_time

            if duration < 1:
                self.ulog.print_and_logger_info(message=f'\nScanned ~{rownum} rows from {filename} in {duration:.2f} seconds.')
            return df

        except FileNotFoundError:
            self.ulog.print_and_logger_info(f'File {ufile} not found')
            exit(1)

    def _estimate_scan_time(self, filename: str) -> str:
        """
        Provides an estimate of the scan time of a UMLS file.
        The tqdm progress bar does not work with Polar's scan_csv function,
        because scan_csv is lazy loading and works with the entire file at
        once.
        :param filename: UMLS filename
        """

        # Check whether the file is scanned or read.
        scanfiles = self.cfg.get_section(section='scanestimates')
        message = ''
        if filename.lower() in scanfiles.keys():

            # Provide an estimate, based on the developer machine.
            scanos = self.cfg.get_value(section='scanestimates', key='os')
            scanmemory = self.cfg.get_value(section='scanestimates', key='memory')
            scantime = self.cfg.get_value(section='scanestimates', key=filename)
            if int(scantime) > 1:
                message = f'\nThe historical time to scan the entire {filename} on {scanmemory} RAM {scanos} machine is < {scantime} seconds.'

        return message


    def _get_clean_file(self, filename: str) -> str:
        """
        Pre-processes a UMLS file to remove double-quoted strings.
        :param filename: UMLS filename

        :return: the path to the cleaned file.

        Assumes that the file has RRF as an extension.
        """

        dirtyfile = os.path.join(self.cfg.get_value(section='directories', key='umls_dir'), 'META', filename + '.RRF')
        cleanfile = os.path.join(self.cfg.get_value(section='directories', key='output_dir'), filename + '.RRF')

        if os.path.exists(cleanfile):
            self.ulog.print_and_logger_warning(
                f'\nUsing existing cleaned file {cleanfile}. Delete this file to force pre-processing.')
        else:
            self.ulog.print_and_logger_info(f'Cleaning file: {dirtyfile}...')

            # Get the total number of lines in the input file.
            with open(dirtyfile, "r", encoding="utf-8") as infile:
                total_lines = sum(1 for _ in infile)

            # Process the file with tqdm progress bar.
            with open(dirtyfile, "r", encoding="utf-8") as infile, open(cleanfile, "w", encoding="utf-8") as outfile:
                with tqdm(total=total_lines, desc="Cleaning", colour="white") as pbar:
                    for line in infile:
                        # Replace improperly escaped quotes.
                        fixed_line = line.replace('"', '')
                        outfile.write(fixed_line)
                        pbar.update(1)  # Update the progress bar for each processed line

        return cleanfile

    def _scan_with_timer(self,filename:str, separator:str,
                         checksuppress:bool, checkenglish:bool, checkcurver:bool,
                         new_columns: list, n_rows:int,
                         refresh_interval: float = 0.2,

                         ) -> pl.DataFrame:
        """
        The Polars scan_csv function is not amenable to wrapping in a
        tqdm progress indicator. This function starts a separate thread that
        displays a timer around the scan.

        :param filename: path to file to scan
        :param separator: separator
        :param new_columns: new_columns
        :param n_rows: n_rows
        :param checksuppress: flag - filter on SUPPRESS field, if it is a column
        :param checkenglish: flag - filter on LAT=ENG, if LAT is a column
        :param checkcurver: flag - filter on CURVER, if CURVER is a column

        :return: the DataFrame
        """

        # Start a timer for the scan and collection.
        utimer = UbkgTimer(display_msg=f"Scanning {filename}")

        try:
            # Scan file.
            lf = (pl.scan_csv(filename,
                             separator=separator,
                             has_header=False,
                             new_columns=new_columns,
                             n_rows=n_rows)
                  .fill_null("") # replace nulls with blank strings
                  .unique()) # drop duplicates

            # Optional filtering.
            if checksuppress:
                lf = (lf.filter(pl.col('SUPPRESS') != 'O'))
            if checkenglish:
                lf = (lf.filter(pl.col('LAT') == 'ENG'))
            if checkcurver:
                lf = (lf.filter(pl.col('CURVER') == 'Y'))

            # Trigger the scan and compute. This is the blocking operation that is timed.
            df = lf.collect()

        finally:
            # Stop timer.
            utimer.stop()

        return df

    def _get_inverse_relationships(self) -> pl.DataFrame:
        """
        Builds a dataframe of information on "inverse" relationships, defined
        to be the relationship in a bilateral pair of relationships with
        term that is alphabetically first.

        For example, for the bilateral pair has_nerve_supply/nerve_supply_of,
        "has_nerve_supply" is considered the inverse relationship.

        For forward/inverse relationship pairs, MRDOC contains two records, with each relationship
        in the pair in both the VALUE and EXPL columns.

        Example:
        DOCKEY | VALUE       | TYPE          | EXPL         |
        RELA | nerve_supply_of | rela_inverse | has_nerve_supply |
        RELA | has_nerve_supply | rela_inverse | nerve_supply_of |

        Resolve the paired rows and select as the "inverse relationship"
        the relationship for which the value is the first alphabetically
        in the pair.
        """

        df_inverse_rel_pairs = (self.get_umls_file(filename='MRDOC')
                                .filter(pl.col('DOCKEY') == 'RELA')
                                .filter(pl.col('TYPE') == 'rela_inverse'))


        df_inverse = (
            df_inverse_rel_pairs
            .with_columns(
                # Create an identifier for pairs, considering VALUE and EXPL as reciprocal links.
                pl.when(pl.col("VALUE") < pl.col("EXPL"))
                .then(pl.col("VALUE") + "~" + pl.col("EXPL"))
                .otherwise(pl.col("EXPL") + "~" + pl.col("VALUE"))
                .alias("pair_group")
            )
            .group_by("pair_group")  # Group on the pair identifier
            .agg([
                # Retain only the row with the alphabetically first VALUE
                pl.col("VALUE").sort().first().alias("VALUE"),
                pl.col("DOCKEY").first(),
                pl.col("TYPE").first(),
                pl.col("EXPL").first(),
            ])
        )

        df_inverse = df_inverse.with_columns((pl.col("VALUE")).alias("inverse_relationship"))

        # Print out the inverse relationships for comparison with the
        # manually-curated version.
        out_file = os.path.join(self.cfg.get_value(section='directories', key='output_dir'), 'inverse_relationships.csv')
        # ulog.print_and_logger_info(f'(List of filtered inverse relationships at {out_file})')
        df_inverse.select(pl.col('VALUE')).sort('VALUE').write_csv(out_file)

        out_file = os.path.join(self.cfg.get_value(section='directories', key='output_dir'),
                                'relationship_pairs.csv')
        df_inverse_rel_pairs.write_csv(out_file)

        return df_inverse.select('inverse_relationship')

    def _get_concept_concept_rels(self) -> pl.DataFrame:
        """
        Builds a DataFrame of information on the relationships
        between UMLS concepts.
        """
        print('')
        self.ulog.print_and_logger_info(f'Building information on concept-concept relationships...')

        # Obtain non-suppressed relationships from MRREL.RRF.
        colrels = ['CUI1', 'CUI2', 'REL', 'RELA', 'SAB']
        # CUI1 - CUI of end concept
        # CUI2 - CUI of start concept
        # REL - required id for relationship (usually 2 character)
        # RELA - optional, more specific description (usually delimited)
        # SAB - source of relationship

        df_mrrel = self.get_umls_file(filename='MRREL', cols=colrels)

        # Filter out self-referential relationships.
        self.ulog.print_and_logger_info(f'Filtering out self-referential relationships...')
        df_self_edge = df_mrrel.filter(pl.col("CUI1") == pl.col("CUI2"))
        self_edge_file = os.path.join(self.cfg.get_value(section='directories', key='output_dir'), 'self_relations.csv')
        df_self_edge.write_csv(self_edge_file)

        df_mrrel = df_mrrel.filter(pl.col("CUI1") != pl.col("CUI2"))

        # Get the relationship label--the value of RELA if not null else
        # the value of REL.
        df_mrrel = df_mrrel.with_columns(
            pl.when(pl.col("RELA").is_null())
            .then(pl.col("REL"))
            .otherwise(pl.col("RELA"))
            .alias("rel_label")
        )
        colrels.append('rel_label')

        # Filter to English-language SABs.
        # ulog.print_and_logger_info(f'--Filtering to relationships from English-language SABs using MRSAB.RRF...')
        colsabs = ['RSAB', 'LAT']
        # RSAB - SAB acronym
        # LAT - language
        df_mrsab = self.get_umls_file(filename='MRSAB', cols=colsabs)
        # Filter to relationships defined in English-language SABs.
        df_rel = (
            df_mrrel.join(
                df_mrsab,
                how='inner',
                left_on='SAB',
                right_on='RSAB',
                maintain_order='left')
            .unique())

        # Filter out inverse relationships.
        df_inverse = self._get_inverse_relationships()

        # anti-join
        df_rel = ((df_rel.join(
            df_inverse,
            how='anti',
            left_on='RELA',
            right_on='inverse_relationship')))

        df_rel = df_rel.select(colrels).unique()

        return df_rel

    def _get_concept_code_rels(self) -> pl.DataFrame:
        """
        Builds a DataFrame of information on the relationships
        between UMLS concepts (CUIs) and codes from UMLS vocabularies.
        """

        print('')
        self.ulog.print_and_logger_info('Building data for concept-code relationships...')

        # Obtain non-suppressed, English-only relationships.
        # MRCONSO.RRF contains fields that include double quotes.
        # Polars considers these as incorrectly escaped fields.
        # It is necessary to pre-process the file before reading it.

        col_conso = ['STR', 'SAB', 'CODE', 'TTY', 'CUI', 'AUI', 'ISPREF', 'STT', 'TS']
        # MRCONSO contains one row per atom -- i.e., unique combination of CUI/code/term
        # AUI - atom identifier, equivalent to primary key
        # CUI - CUI of concept
        # SAB - SAB of source of code linked to concept
        # CODE - ID of code in SAB
        # TTY - term type of term
        # STR - term string
        # STT - string type--in particular, 'PF' means preferred term
        # ISPREF - whether the term is the preferred for the concept
        # TS - term status

        df_mrconso = self.get_umls_file(filename='MRCONSO', cols=col_conso, clean_file=True)

        # Obtain non-suppressed definitions.
        col_def = ['AUI', 'DEF']
        # AUI - identifier
        # DEF - definition string
        # MRDEF.RRF also contains fields that include double quotes, so pre-process.
        df_mrdef = self.get_umls_file(filename='MRDEF', cols=col_def, clean_file=True)

        # Join MRDEF data to MRCONSO data.
        utimer = UbkgTimer(display_msg="Joining MRDEF and MRCONSO")
        start_time = time.time()
        df = df_mrconso.join(
            df_mrdef,
            how='left',
            on='AUI',
            maintain_order='left'
        ).fill_null('')
        utimer.stop()

        utimer = UbkgTimer(display_msg="Standardizing IDs")
        start_time = time.time()
        # Create standardized codeid.
        # Apply the transformations in steps.
        # Step 1: Create `codeid` column
        df = df.with_columns(
            create_codeid(SAB_col=pl.col('SAB'), CODE_col=pl.col('CODE'), codeid_col='codeid')
        )
        # Step 2: Standardize codeid from Step 1 to remove embedded SABs and special characters.
        df = df.with_columns(
            standardize_codeid(codeid_col='codeid')
        )

        # Add a column to terms that resemble CURIEs.
        df = df.with_columns(
            standardize_term(term_col='STR')
        )


        utimer.stop()

        return df

    def _get_semantic_definitions(self) -> pl.DataFrame:
        """
        Builds a DataFrame of information on semantic definitions from SRDEF

        """

        # Obtain information for the Semantic Network nodes from SRDEF.
        colsem = ['RT', 'UI', 'STY_RL', 'DEF']
        # RT - Record type: STY = semantic
        # UI - Unique identifier
        # STY_RL - name of the semantic relation
        # DEF - definition
        df = self.get_umls_file(filename='SRDEF', cols=colsem).unique()
        # Filter to those nodes for which the Relationship Type is "Semantic Type"
        df = df.filter(pl.col('RT') == 'STY')

        return df

    def get_umls_version(self) -> str:
        """
        Obtains the UMLS version from the release.dat file
        :return:
        """

        # The release.dat file should be in the META path of the MetamorphoSys output.
        umls_dir = self.cfg.get_value(section='directories', key='umls_dir')
        rel_file = os.path.join(umls_dir, 'META', 'release.dat')

        try:
            with open(rel_file, 'r') as f:
                lines = f.readlines()
                for l in lines:
                    if 'umls.release.name' in l:
                        return l.split('=')[1].strip()
        except FileNotFoundError:
            return ""
    def get_jkg_version(self)-> str:
        """
        Obtains the JKG version from the VERSION file
        """
        version_path = os.path.join(self.repo_root, 'VERSION')
        with open(version_path, 'r') as f:
            return f.read().strip()



