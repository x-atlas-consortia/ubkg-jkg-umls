"""
refseq.py
Class that obtains definitions of genes from RefSeq.
"""
import os
import time
import requests
import pandas as pd
import polars as pl

from .ubkg_logging import UbkgLogging
from .ubkg_extract import ubkgExtract
from .ubkg_config import UbkgConfigParser

class Refseqapi:

    def _getapikey(self) -> str:

        # Get an API key from a text file in the application directory.
        try:
            apikey_path = os.path.join(os.getcwd(), 'classes', 'apikey.txt')
            with open(apikey_path, 'r') as fapikey:
                apikey = fapikey.read()
        except FileNotFoundError as e:
            self.ulog.print_and_logger_info('Missing file: apikey.txt')
            exit(1)

        return apikey

    def __init__(self, ulog:UbkgLogging, cfg:UbkgConfigParser,
                 uext:ubkgExtract):

        self.ulog = ulog
        self.cfg = cfg
        self.apikey = self._getapikey()
        self.uext = uext

    def get_gene_definition(self, hgnc_id: str) -> str:
        """
        Retrieve a single gene summary/definition from calls to NCBI EUtils.

        :param hgnc_id: a single HGNC ID e.g. 'HGNC:1097'
        :return: gene summary string
        """

        base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

        # NCBI rate limits: 3 requests/sec without API key, 10 requests/sec with API key
        delay = 0.11 if self.apikey else 0.34
        time.sleep(delay)

        # Step 1: Use esearch to find the NCBI Gene ID from the HGNC ID
        esearch_url = f"{base_url}/esearch.fcgi"
        esearch_params = {
            "db": "gene",
            "term": f"{hgnc_id}[xref]",
            "retmode": "json",
            "api_key": self.apikey
        }
        esearch_response = requests.get(esearch_url, params=esearch_params)

        if esearch_response.status_code == 429:
            # Back off and retry once
            time.sleep(2)
            esearch_response = requests.get(esearch_url, params=esearch_params)

        esearch_response.raise_for_status()
        esearch_data = esearch_response.json()

        id_list = esearch_data.get("esearchresult", {}).get("idlist", [])
        if not id_list:
            return ""

        ncbi_gene_id = id_list[0]

        time.sleep(delay)

        # Step 2: Use esummary to get the gene summary
        esummary_url = f"{base_url}/esummary.fcgi"
        esummary_params = {
            "db": "gene",
            "id": ncbi_gene_id,
            "retmode": "json",
            "api_key": self.apikey
        }
        esummary_response = requests.get(esummary_url, params=esummary_params)

        if esummary_response.status_code == 429:
            # Back off and retry once
            time.sleep(2)
            esummary_response = requests.get(esummary_url, params=esummary_params)

        esummary_response.raise_for_status()
        esummary_data = esummary_response.json()

        summary = (esummary_data
                   .get("result", {})
                   .get(ncbi_gene_id, {})
                   .get("summary", ""))

        return summary if summary else ""

    def getrefseqsummaries(self, outdir: str,
                              start: int,
                              chunk: int) -> pd.DataFrame:

        # Check for the existence of a RefSeq file.
        refseqfile = os.path.join(outdir, 'REFSEQ.csv')

        if os.path.isfile(refseqfile):
            self.ulog.print_and_logger_warning(
                'Using existing file REFSEQ.csv. Delete this file to force new download.')
            return self.uext.read_csv_with_progress_bar(path=refseqfile)
        else:
            return self.getnewrefseqsummaries(outdir=outdir, start=start, chunk=chunk)


    def getnewrefseqsummaries(self, outdir: str,
                           start: int,
                           chunk: int) -> pd.DataFrame:
        """
        Calls endpoints of the NCBI EUtils to obtain a list of summaries of a block of
        human gene Entrez IDs.

        :param outdir: output directory
        :param start: offset for the full set of Entrez IDs--e.g., 50,001
        :param chunk: number of genes to extract--e.g., 50,000
        :return: DataFrame with Entrez ID, HGNC ID, and definition
        """

        # ReqSeq API base url
        baseurl = 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/'

        listcodes = []
        listdefs = []
        listhgnc = []
        listhgncname = []

        query = 'human[orgn]AND+alive[prop]'
        db = 'gene'
        params = f'retmode=json&db={db}&apikey={self.apikey}'

        retstart = start
        retcount = start + chunk
        retmax = 499

        self.ulog.print_and_logger_info(
            f'Obtaining RefSeq summaries for genes from NCBI eUTILs in blocks of {retmax}...')

        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}

        # Process chunks of gene IDs.
        while retstart < (start + chunk):

            print(f'-- range: {retstart} to {retstart + retmax} (total count={retcount})')
            time.sleep(1)

            if retstart + retmax > retcount:
                retmax = retcount

            # Call the esearch endpoint to obtain a set of gene uuids.
            esearch = f'esearch.fcgi?&{params}&usehistory=y&retmax={retmax}&retstart={retstart}&term={query}'
            urlsearch = f'{baseurl}{esearch}'

            self.ulog.print_and_logger_info('-- Getting list of uuids...')
            responsesearch = self.uext.getresponsejson(urlsearch)

            esearchresult = responsesearch.get('esearchresult')
            retcount = int(esearchresult.get('count'))
            webenv = esearchresult.get('webenv')
            querykey = esearchresult.get('querykey')

            # request to obtain RefSeq summary information for set of gene uuids
            esummary = f'esummary.fcgi?&{params}&WebEnv={webenv}&query_key={querykey}&retstart={retstart}&retmax={retmax}'
            urlsummary = f'{baseurl}{esummary}'

            self.ulog.print_and_logger_info('-- Getting summary information for set of uuids...')
            responsesummaryjson = self.uext.getresponsejson(urlsummary)

            if responsesummaryjson is None:
                self.ulog.print_and_logger_info('Empty response from API in response to URL:')
                self.ulog.print_and_logger_info(f'{esummary}')
                break

            result = responsesummaryjson.get('result')
            uids = result.get('uids')

            for uid in uids:
                gene = result.get(uid)
                if gene is not None:
                    summary = gene.get('summary', '')

                    code = uid
                    hgnc_symbol = gene.get('nomenclaturesymbol', '')
                    hgnc_name = gene.get('nomenclaturename', '')
                    summary = gene.get('summary', '').strip()

                    listcodes.append(code)
                    listdefs.append(summary)
                    listhgnc.append(hgnc_symbol)
                    listhgncname.append(hgnc_name)


            retstart = retstart + retmax + 1

        # Build return DataFrame
        dfout = pd.DataFrame({
            'Entrez': listcodes,
            'HGNC_ID': listhgnc,
            'HGNC_name': listhgncname,
            'definition': listdefs
        })
        dfout = dfout.drop_duplicates()
        dfout = dfout.dropna(subset=['definition'])
        dfout = dfout[dfout['definition'] != '']
        dfout = dfout[dfout['HGNC_ID'] != '']

        # Write to output
        os.system(f"mkdir -p {outdir}")
        fout = os.path.join(outdir, 'REFSEQ.csv')
        self.uext.to_csv_with_progress_bar(df=dfout, path=fout)

        return dfout

    def get_identifiers_from_gene_info(self, output_dir: str) -> pl.DataFrame:
        """
        Obtains current identifiers on human genes in NCBI.
        :param output_dir: output directory
        :return: DataFrame
        """

        self.ulog.print_and_logger_info('Getting HGNC definitions from NCBI.')

        baseurl = self.cfg.get_value(section='refseq', key='refseq_url')

        gene_info_filtered_path = os.path.join(output_dir, 'gene_info_filtered.tsv')

        if os.path.exists(gene_info_filtered_path):
            self.ulog.print_and_logger_warning(
                'Reading gene definitions from existing gene_info_filtered.txt. Delete this file to force download.')
            df_filter= self.uext.polars_scan_csv_with_timer(filename=gene_info_filtered_path, separator='\t')
        else:
            # Download gzipped file.
            self.ulog.print_and_logger_info(
                'Building new gene_info_filtered.txt from RefSeq.')

            gene_info_path = self.uext.get_gzipped_file(zip_url=baseurl,
                                                        zip_path=output_dir,
                                                        extract_path=output_dir,
                                                        zipfilename='gene_info.gz',
                                                        outfilename='gene_info.txt')

            self.ulog.print_and_logger_info('Scanning gene_info.txt')
            df_gene_info = self.uext.polars_scan_csv_with_timer(filename=gene_info_path, separator='\t')


            # Filter to human and mouse genes only
            self.ulog.print_and_logger_info('Filtering to human and mouse genes.')
            df_filter = (
                df_gene_info
                .filter(pl.col('#tax_id').is_in([9606, 10090]))
                .with_columns([
                    pl.col('dbXrefs').str.extract(r'(HGNC:\d+)', 0).alias('HGNC'),
                    pl.col('dbXrefs').str.extract(r'(MGI:\d+)', 0).alias('MGI'),
                    pl.col('dbXrefs').str.extract(r'(Ensembl:[A-Z0-9]+)', 0).alias('Ensembl'),
                ])
                .select(['GeneID', 'Symbol','HGNC', 'MGI', 'Ensembl', 'description'])
            )

            # Write filtered file
            self.ulog.print_and_logger_info(f'Writing filtered gene info to {gene_info_filtered_path}')
            df_filter.write_csv(gene_info_filtered_path, separator='\t')

        return df_filter