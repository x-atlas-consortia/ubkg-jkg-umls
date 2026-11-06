# Universal Biomedical Knowledge Graph - JSON Knowledge Graph format (UBKG-JKG)
## UMLS to JKG Converter

# Background
## UBKG
UBKG is a framework for building and distributing knowledge graphs of assertions obtained 
from multiple biomedical data sources. The UBKG extends the _concept-code synonymy_ of the UMLS 
(where a single _concept_ can be represented by _codes_ in multiple vocabularies), combining UMLS
concept data with information from other sources. For a detailed explanation of the 
UBKG, consult the [UBKG documentation](https://ubkg.docs.xconsortia.org/).

The initial release of the UBKG was a knowledge graph with a schema that adhered closely to the UMLS architecture. 
In particular, almost all properties of concepts and codes (representations of concepts in vocabularies), including
terms and definitions, were nodes in the UBKG. Although this schema supported knowledge graph analysis, it
was complex and bound both to the UMLS architecture and the neo4j platform. 

## JKG
The [JSON Knowledge Graph](https://github.com/x-atlas-consortia/json-knowledge-graph), or JKG schema supports a simple,
platform-agnostic structure for a knowledge graph, as well as specification for transferring data between knowledge graphs.
The JKG schema consists of two types of arrays:
- **nodes**, with elements for 
  - sources (e.g., vocabularies)
  - semantic types
  - relationships
  - concepts
  - terms
- **rels**, with elements that describe relationships
  - between semantic types (i.e., that describes a semantic type hierarchy)
  - between concepts and concepts
  - between concepts and codes

## umls2jkg
Applications in this repository convert source data obtained from the UMLS to a JSON file that
conforms to the JKG schema.

#### Components
1. **umls2jkg.py**:  Python application that:
   - reads UMLS data files
   - transforms UMLS data to JKG format
   - writes to output
2. **umls2jkg.sh**:  Shell script that:
   - establishes a Python virtual environment for the **umls2kg.py** script
   - executes the **umls2kg.py** script
3. **umls2kjg.ini**: configuration file that drives the execution of the **umls2kg.py** script

# Prerequisites
## Metathesaurus and Semantic Network files
The [UMLS](https://www.ncbi.nlm.nih.gov/books/NBK9676/) includes:
- the [Metathesaurus](https://www.ncbi.nlm.nih.gov/books/NBK9684/)
- the [Semantic Network](https://www.ncbi.nlm.nih.gov/books/NBK9679/)

The UMLS provides data files for the Metathesaurus and Semantic Network 
by means of the [MetamorphoSys](https://www.ncbi.nlm.nih.gov/books/NBK9683/) application. 

The **umls2kjg** application reads from a subset of the data files from both the
Metathesaurus and Semantic Network.

## MetamorphoSys 
The MetamorphoSys application allows the selection of subsets of UMLS data. Configuration options include:
- selection of specific SABs
- exclusion by license restriction

The **umls2kjg** application expects only that data from both the Metathesaurus and Semantic Network are available.
The application does not expect particular SAB content.

Refer to the [UMLS Reference Manual](https://www.ncbi.nlm.nih.gov/books/NBK9683/) for instructions on configuring and running MetamorphoSys.

#### Configuring MetamorphoSys
The UMLS provides MetamorphoSys as part of a [UMLS Full Release](https://www.nlm.nih.gov/research/umls/licensedcontent/umlsknowledgesources.html).
Metamorphosys runs in Windows, Linux, and MacOs. 

1. Download the UMLS full release Zip.
2. Expand the release Zip.
3. Navigate to the release root.
4. Expand the mmsys.zip archive.
5. Copy the **release.dat** from the mmsys directory up to the release root. MetamorphoSys will not run
on a MacOs machine until the **release.dat** file is in the expected location.

####  Override MacOs Tahoe security
MetamorphoSys is a Java application that uses a number of dynamic library (.dylib) files. 

Beginning with the Tahoe release, MacOs security does not allow the automatic download of dynamic libraries. 
For the initial run of MetamorphoSys, you will need to allow downloads of each dylib file explicitly. 

The process is tedious, but straightforward:
1. Open **System Settings>Privacy & Security > Security**. 
2. Start a Terminal session. 
3. Navigate to the mmsys directory. 
4. Run ./run_mac.sh. 
5. MetamorphoSys will start trying to download dynamic library files, triggering error loops that you must resolve for each dynamic library.

##### Error loop
1. Security displays an modal error box stating that it cannot load the dylib file. ![img_5.png](img_5.png)
2. Select **Done**, not **Move to Trash**. This will occur 3-5 times in succession before MetamorphoSys raises a Java exception.
2. Security refers to the dylib file in question with the button **Allow anyway**. Select this button. ![img_6.png](img_6.png)
3. Run MetamorphoSys again. Security will display the modal error with the additional button **open anyway**. Click this button. ![img_7.png](img_7.png)
4. Security will ask for an administrative password. Provide the administrative password.
5. MetamorphoSys will move to the next dylib.

#### Run MetamorphoSys on the full release.

After overriding security for all dynamic libraries, the MetamorphoSys splash screen will appear: ![img_8.png](img_8.png). Click **Install UMLS**.
The home page of MetamorphoSys will appear.
1. Specify the source directory. Navigate to the release root.
2. Specify the destination directory. This location is up to you.
3. Click OK.
![img_4.png](img_4.png)

4. A **Validation Log** screen will appear. Click **Continue**.
5. The **MetamorphoSys Progress..** window will appear at top left.
6. The **Choose MetamorphoSys Configuration** window will appear below the MetamorphoSys splash screen. Click **New Configuration...**.
7. The **License Agreement** window will appear. Click **Accept**. (The University of Pittsburgh has a license to distribute UMLS content.)
8. The **Select Default Subset Configuration** window will appear. Select the **Active Subset**.
9. An multi-tab window will appear that summarizes configuration options. 
10. From the **Done** menu, select **Start Subset**.

#### File export
A full release Zip is very large. For example, the 2025AB release file expands to almost 40 GB.

## Machine characteristics
### Operating System
The **umls2jkg** application was developed on a MacOs machine running Tahoe 26.2.

### Memory
It is recommended that the host machine have at least 32 GB of RAM.
Even with this amount of RAM, it is likely that the machine will need disk space for swapping, because of the need to load large files into memory.

### Disk space
The host machine should have at least enough disk space to accomodate:
* data files
* output files
* memory swap space

Disk space requirements include:

| Type of file          | Disk space (GB) |
|-----------------------|-----------------|
| UMLS full release ZIP | >5              |
| MetamorphoSys files   | >50             |
| JKG.JSON output       | ~5 GB           |


# Application configuration
The **umls2kjg.ini** file drives the operation of the **umls2jkg.py** script.
It is likely that the user will need to edit only a few values in the 
configuration file, including:

| section     | key             | value                                                                                                                    |
|-------------|-----------------|--------------------------------------------------------------------------------------------------------------------------|
| directories | umls_dir        | absolute path to the root folder of the MetamorphoSys output--i.e., the parent directory of the META and NET directories |
|             | output_dir      | absolute path to which to write output                                                                                   |
| json_out    | output_filename | file name of the JKG JSON output                                                                                         |
|             | pretty          | true=write output with indentation                                                                                       |
|             | indent          | indentation when pretty=true                                                                                             |
| debug       | debug_n_rows    | The number of rows to read from data files. A value of 0 will result in all rows being read                              |

## Pretty printing
It is possible to "pretty print" the JKG JSON output file--i.e., generate output with spacing and indentation. 
Although pretty-printing increases the legibility of the output file, it also dramatically increases its size.
## Software
### Python
The **umls2jkg** application was developed in Python 3.13.

### Packages
**umls2jkg** uses the following packages:
- [Polars](https://pola.rs/) to analyze the extremely large UMLS data files
- [Pandas](https://pandas.pydata.org/) to obtain gene information from RefSeq
- [tqdm](https://tqdm.github.io/) for progress indicators

# Application workflow
![img_1.png](img_1.png)

The **umls2kg** application reads and combines content from UMLS data files to 
build elements in the arrays of the JGK JSON file.

Counts of elements are based on the default export from MetamorphoSys.

## nodes

| type                        | description                                                                  | # elements |
|-----------------------------|------------------------------------------------------------------------------|------------|
| Source                      | source vocabularies, including the UMLS                                      | ~110       |
| Semantic Network Node_Label | Semantic Network nodes                                                       | ~130       |
| Rel_Label                   | relationship predicates used between concepts and between concepts and codes | ~500       |
| Concept                     | concepts                                                                     | ~3.2M      |
| Term                        | code terms (e.g., PT)                                                        | ~7.6M      |                                                                    

## rels

| type                | description                                         | # elements |
|---------------------|-----------------------------------------------------|------------|
| Semantic Network    | relationships between nodes in the Semantic Network | ~600       |
| concept-to-concept  | relationships between concepts                      | ~10.4M     |
| concept-to-code     | relationships between concepts and codes            | ~8.8M      |
| NDC code-to-concept | mapping of NDC codes to RxNorm concepts             | ~250K      |


# File processing and timing

The time that the **umls2jkg** application requires to process UMlS data depends on 
a number of factors. However, it is possible to provide reasonable estimates for timing.

## Scanning and reading
The Polars package works with large files using columnar processing and lazy loading. 
This results in very fast processing of large files: for example, **umls2jkg** can scan a 9 GB file (MRREL.RRF) in less than 40 seconds.

Unlike row-based packages like Pandas, Polars processes an entire file at once. It is often not possible to track the progress of processing.

To give an idea of how long a file might take to process, **umls2jkg** application provides historical estimates of the times required to process files, under
ideal conditions. 

## Cleaning
Three Metathesaurus files (MRCONSO.RRF; MRDEF.RRF; and MRSAT.RRF) contain rows with text that includes quote characters. 
Because Polars cannot scan files with these characters, it is necessary to remove quote characters from the files.

**umls2jkg** looks for cleaned versions of these files in the output directory. If a file is not present, the application creates a cleaned version of the file.

### Standardized string formats
#### Relationships
The [neo4 naming rules](https://neo4j.com/docs/cypher-manual/current/syntax/naming/) specify that relationship names:
1. Contain only alphanumeric characters or the underscore.
2. Start with a alphabetic character.

Although a relationship can include special characters or start with a number, Cypher queries that refer 
to the relationship must escape the relationship name with backticks (`). To avoid the need
for backticks, relationships in the UBKG are reformatted as follows:

| Characters                        | Change                    | Example                       |
|-----------------------------------|---------------------------|-------------------------------|
| hyphen                            | replaced with underscore  | **abc-def** to **abc_def**    |
| other non-alphanumeric characters | replaced with underscores | **abc (def)** to **abc_def_** |
| leading numeric character         | preceded with 'REL_'      | **1abc** to **REL_1abc**      |

#### Codes
Some codes in the UMLS contain characters that will fail schema validation. These codes
currently are found in the CDT and HL7V2.5 SABs. The script replaces special characters 
in these codes with the underscore.

### Concepts without preferred terms
Information on UMLS concepts is found in MRCONSO.RRF. Most UMLS concepts will have a row in
MRCONSO.RRF that corresponds to a preferred term for the concept, satisfying the following criteria:
1. The Atom Status (TS) is preferred  (ISPREF=Y). 
2. The String type (STT) is "PF" (Preferred form of term). 
3. The Term Status is "P" (Preferred LUI of the CUI).

A number of concepts from the CHV and MSH SABs do not have preferred term rows in MRCONSO.RRF. For 
example, many concepts have rows where ISPREF=Y but STT=VC, corresponding to a "case variant of a preferred term".

The script assigns blank preferred terms to concepts without rows in MRCONSO.RRF that do not satisfy the preferred term criteria.

## Special cases
### NaN 
The SCN11A gene and its associated gene product (NaN) have terms or synonyms with the string value of "NaN" in the UMLS source data (CUIs C1419854 and C5958837).
The string "NaN" is a reserved word in Python, corresponding to np.NaN (Not a Number). To avoid problems in Python scripts, the 
term "NaN" is changed to "Nan (term)".


## Historical processing times
The following times were for the processing of the full 2025AB release, on a MacOs machine with 32GB RAM and no other processes competing for resources.

Time to scan, clean, and process files: 11 minutes, 14 seconds.

| Step              | time (mm:ss) | file size |
|-------------------|--------------|-----------|
| scan MRREL.RRF    | 1:10         | 9GB       |
| scan MRSAB.RRF    | <00:01       | <1 MB     |
| scan MRDOC.RRF    | <00:01       | <1 MB     |
| clean MRCONSO.RRF | <00:10       | 2 GB      |
| scan MRCONSO.RRF  | <00:10       | 2 GB      |
| clean MRDEF.RRF   | <00:01       | <1 MB     |
| scan MRDEF.RRF    | <00:01       | <1 MB     |
| scan SRDEF        | <00:01       | <1 MB     |
| build nodes array | <01:00       |           |
| scan SRSTRE1      | < 01:00      | <1 MB     |
| clean MRSAT.RRF   | <01:00       | 9GB       |
| scan MRSAT.RRF    | <03:00       |           |
| build rels array  | <05:00       |           |          |

# Gene descriptions
As part of processing, **umls2jkg** obtains descriptions of genes by querying [RefSeq](https://www.ncbi.nlm.nih.gov/refseq/).
The script writes gene descriptions to a file named REFSEQ.csv, in the same directory as the **jkg.json** output.
If REFSEQ.csv exists in the output directory, the script will use the information in the file instead of querying RefSeq.
Deleting REFSEQ.csv will force the script to regenerate it.


# Output

**umls2jkg** writes all output to the path specified by the _output_dir_ and _output_filename_ keys in **umls2kjg.ini**.

In addition, the application writes cleaned versions of Metathesaurus file to the output directory.



