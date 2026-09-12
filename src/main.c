/*
 * main.c: module principal de PAPP
 *
 * (EL) 15/10/2019 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, no change
 * (EL) 16/07/2012 : v1.34, no change
 * (EL) 05/05/2008 : v1.33, Tous les 'int' deviennent 'long' pour etre sur d'etre sur 4 octets.
 * (EL) 21/04/2008 : v1.32, no change
 * (EL) 02/05/2007 : v1.31, changements dans les #include
 * (EL) 06/04/2007 : changement des 'fopen()' en 'myfopen_in_subfolder()'
 * (EL) 03/04/2007 : Changement de l'affichage initial.
 * (EL) 30/03/2007 : Ecriture de la fonction 'Init_Subfolder()' qui cree
                     un sous-dossier a partir du fullname du tournoi et modifie les noms
                     des fichiers class/result/ronde...
 * (EL) 06/02/2007 : Test de la presence d'un ancien fichier intermediaire
                     ou de l'absence de ce fichier. Dans ce cas, demande
                     les caracteristiques et sauvegarde un nouveau fichier intermediaire.
                     Ecriture de la fonction 'Demander_caracteristiques_tournoi'
 * (EL) 04/02/2007 : Test de la presence du type precedent
 *                   du fichier de config -> affichage d'un warning.
                     Ecriture de la fonction 'warning_old_papp_cfg'.
 * (EL) 13/01/2007 : v1.30 by E. Lazard, no change
 *
 ****
 *
 *  * main.c: PAPP main module
 *
 * (EL) 15/10/2019 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, no change
 * (EL) 16/07/2012 : v1.34, no change
 * (EL) 05/05/2008 : v1.33, All 'int' become 'long' to force 4 bytes storage.
 * (EL) 21/04/2008 : v1.32, no change
 * (EL) 02/05/2007 : v1.31, changements dans les #include
 * (EL) 06/04/2007 : change all 'fopen()' to 'myfopen_in_subfolder()'
 * (EL) 03/04/2007 : Changes in initial display.
 * (EL) 30/03/2007 : Writing of function 'Init_Subfolder()' which creates a subfolder
 *                   from the tournament fullname and modifies names of standing/result/round files
 * (EL) 06/02/2007 : Tests if no or an old workfile is present. In that case,
                     asks for tournament informations and save a new workfile.
                     Function 'askTournamentInformation' is written.
 * (EL) 04/02/2007 : Test if an old version of configuration file is present,
 *                   in which case a warning is display. Function 'warning_old_papp_cfg' is written.
 * (EL) 13/01/2007 : v1.30 by E. Lazard, no change

 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include <ctype.h>
#include <errno.h>
#include <time.h>

#if defined(CYGWIN) || defined(MACOSX) || defined(LINUX) || defined(PAPP_MINGW)
	#include <signal.h>
#else
	#include <sys/signal.h>
#endif

#if defined(UNIX_BSD) || defined(UNIX_SYSV)
	#include <unistd.h>
	#include <sys/stat.h>
	#include <dirent.h>
#endif


#include "global.h"
#include "player.h"
#include "tournament_json.h"
#include "version.h"
#include "changes.h"
#include "couplage.h"
#include "more.h"
#include "crosstable.h"
#include "discs.h"


static void main_menu(void)	EXITING ;	/* forward declaration */
void messages_copyleft(void);
void WarningNoConfigFile(void);
void warning_old_papp_cfg(void) ;
void warningTwoPlayersFilesFound(char *PlayersFilenamne,
                                      char *PlayersFilename_Alt);
void askTournamentInformation(long fileType) ;
void Sauver_fichier_intermediaire(long type_fichier) ;
void Init_Subfolder(void) ;

/* static void printnewline(void) {} */
/* static void main_menu(void) /-- decl.forward --/ */

#ifdef ENGLISH
# define MSG_WELCOME    "%s (%s) by Thierry Bousch\n"
# define MSG_CANTLOCK   "can't lock file.\n" \
						"\nIt may be that you don't have execute and write privileges\n" \
						"in that directory.\n"
# define MSG_LOCKED     "the program seems already running.\n" \
                        "\nPerhaps there is an old lockfile (from an old PAPP session)\n" \
                        "in the current directory.\n"
# define BAD_CONFIG     "bad configuration file"
# define BAD_PLAYERS    "bad players file"
# define BAD_NEWP       "bad new-players file"
# define BAD_SAVE       "bad save file"
# define MSG_PLAYERS    "There are %ld players in the database.\n"
# define MSG_CROSS_MEMORY "Not enough memory to generate the cross-table."
# define MSG_NAME_TRN   "         * Name of tournament? "
# define MSG_NBR_ROUNDS "         * How many rounds? "
# define MSG_RROBIN     "           Round-robin between 1 and %ld players.\n"
# define MSG_BQ         "\n         * Brightwell constant used for tie-breaking\n           counting one point per victory\n           (%.0f as default, enter 0 to use disc-count as tie-break)? "
# define PRESS_KEY      "Press any key"
#else
# define MSG_WELCOME    "%s (%s) par Thierry Bousch\n"
# define MSG_CANTLOCK   "verrouillage impossible.\n" \
						"\nPeut-etre n'avez-vous pas les droits d'execution et d'ecriture\n" \
						"sur ce repertoire.\n"
# define MSG_LOCKED     "le programme semble deja en cours d'execution.\n" \
                        "\nPeut-etre y a-t-il un vieux lockfile (d'une vieille session PAPP)\n" \
                        "dans le repertoire courant.\n"
# define BAD_CONFIG     "fichier de configuration incorrect"
# define BAD_PLAYERS    "fichier des joueurs incorrect"
# define BAD_NEWP       "fichier des nouveaux incorrect"
# define BAD_SAVE       "fichier intermediaire corrompu"
# define MSG_PLAYERS    "Il y a %ld joueurs dans la base.\n"
# define MSG_CROSS_MEMORY "Pas assez de memoire pour la cross-table."
# define MSG_NAME_TRN   "         * Nom du tournoi ? "
# define MSG_NBR_ROUNDS "         * Combien de rondes ? "
# define MSG_RROBIN     "           Toutes-rondes entre 1 et %ld joueurs.\n"
# define MSG_BQ         "\n         * Constante de Brightwell pour le departage\n           en comptant un point par victoire\n           (%.0f par defaut, entrez 0 pour un departage aux pions) ? "
# define PRESS_KEY      "Appuyer sur une touche"
#endif

#ifdef ENGLISH
# define MSG_TOURNAMENT_CHOICE "\n      Choose a tournament action:\n"
# define MSG_TOURNAMENT_RESUME "        R - Resume the current tournament\n"
# define MSG_TOURNAMENT_NEW    "        N - Create a new tournament\n"
# define MSG_TOURNAMENT_RESTORE "        %ld - Restore %s\n"
# define MSG_TOURNAMENT_CHOICE_PROMPT "      Your choice? "
# define MSG_TOURNAMENT_INDEX_ERROR "cannot read the tournament index file"
# define MSG_TOURNAMENT_ARCHIVE_ERROR "cannot preserve the current tournament"
# define MSG_TOURNAMENT_RESTORE_ERROR "cannot restore the selected tournament"
# define MSG_TOURNAMENT_INDEX_WRITE_ERROR "cannot save the tournament index"
#else
# define MSG_TOURNAMENT_CHOICE "\n      Choisissez une action pour le tournoi :\n"
# define MSG_TOURNAMENT_RESUME "        R - Reprendre le tournoi courant\n"
# define MSG_TOURNAMENT_NEW    "        N - Creer un nouveau tournoi\n"
# define MSG_TOURNAMENT_RESTORE "        %ld - Restaurer %s\n"
# define MSG_TOURNAMENT_CHOICE_PROMPT "      Votre choix ? "
# define MSG_TOURNAMENT_INDEX_ERROR "impossible de lire l'index des tournois"
# define MSG_TOURNAMENT_ARCHIVE_ERROR "impossible de conserver le tournoi courant"
# define MSG_TOURNAMENT_RESTORE_ERROR "impossible de restaurer le tournoi choisi"
# define MSG_TOURNAMENT_INDEX_WRITE_ERROR "impossible d'enregistrer l'index des tournois"
#endif

#define TOURNAMENT_PATH_LENGTH       2048
#define TOURNAMENT_NAME_LENGTH        256
#define TOURNAMENT_DATE_LENGTH         32
#define TOURNAMENT_RECORDS_MAX        128
#define TOURNAMENT_INDEX_FILENAME     "papp-tournaments.txt"
#define CURRENT_TOURNAMENT_FILENAME   "papp-current-tournament.txt"
#define ARCHIVED_WORKFILE_FILENAME    "papp-internal-workfile.txt"
#define TOURNAMENT_RESUME             (-1)
#define TOURNAMENT_NEW                (-2)

typedef struct tournament_record {
    char folder[TOURNAMENT_PATH_LENGTH];
    char name[TOURNAMENT_NAME_LENGTH];
    char date[TOURNAMENT_DATE_LENGTH];
} TournamentRecord;

static void buildTournamentFolder(const char *name, char *folder);
static long loadTournamentRecords(TournamentRecord records[]);
static long saveTournamentRecords(TournamentRecord records[], long count);
static long loadCurrentTournamentFolder(const char *name, const char *date,
                                        char *folder);
static long saveCurrentTournamentFolder(const char *folder);
static long archiveCurrentTournament(const char *folder,
                                     TournamentRecord records[], long *count);
static long restoreTournament(const TournamentRecord *record);
static void removeTournamentRecord(TournamentRecord records[], long *count,
                                   long index);
static long chooseTournament(TournamentRecord records[], long count);
static long makeNewTournamentFolder(const TournamentRecord records[], long count,
                                    char *folder);
static void resetTournamentState(void);



void messages_copyleft(void) {
	clearScreen();
	printf("\n\n"
#ifdef ENGLISH
		   "        %s (pairing program)  [%s]\n"
		   "\n"
		   "        Written by Thierry Bousch <bousch@topo.math.u-psud.fr>\n"
		   "        Suggestions to <Emmanuel.Lazard-AT-katouche.fr>\n"
		   "\n"
		   "        This is free software; see the file \"COPYING\"\n"
		   "        for distribution conditions.\n\n"
		   "        You can inscribe at most %d players, and there is\n"
		   "        a maximum of %d rounds. There are %ld players in \n"
		   "        the file.\n"
#else
		   "        %s (programme d'appariements)  [%s]\n"
		   "\n"
		   "        Auteur : Thierry Bousch <bousch-AT-topo.math.u-psud.fr>\n"
		   "        Suggestions : <Emmanuel.Lazard-AT-katouche.fr>\n"
		   "\n"
		   "        Ce logiciel est librement redistribuable sous les\n"
		   "        conditions GNU decrites dans le fichier \"COPYING\".\n\n"
		   "        On peut inscrire et apparier %d joueurs au plus,\n"
		   "        avec un maximum de %d rondes. Il y a %ld joueurs \n"
		   "        dans la base.\n"
#endif
		   , VERSION, __DATE__, MAX_REGISTERED, NMAX_ROUNDS, countPlayersInDatabase());
}

void displayConfigBanner(void) {
	printf("\n\n"
#ifdef ENGLISH
		   "   **************************************************************\n"
		   "   *                     Configuration file                     *\n"
		   "   **************************************************************\n"
#else
		   "   **************************************************************\n"
		   "   *                  Fichier de configuration                  *\n"
		   "   **************************************************************\n"
#endif
		   );
}

void WarningNoConfigFile(void) {
	beep();
	printf("\n"
#ifdef ENGLISH
		   "      WARNING: I can't find the configuration file \"%s\".\n"
		   "\n"
		   "      I will try to create, in the current directory, a file\n"
		   "      called \"%s\" with default values, but these values\n"
		   "      will probably not fit exactly your requirements for the\n"
		   "      tournament rules, and you should probably try to use\n"
		   "      instead the config file from the official %s\n"
		   "      distribution [www.ffothello.org/info/appa.php].\n"
		   "      Please read the manual for details.\n"
#else
		   "      WARNING : je ne trouve pas le fichier de configuration \"%s\".\n"
		   "\n"
		   "      Je vais essayer de creer, dans le repertoire courant,\n"
		   "      un fichier \"%s\" comprenant des valeurs par\n"
		   "      defaut, mais ces valeurs ne correspondent peut-etre\n"
		   "      pas aux regles de votre tournoi et vous devriez plutot\n"
		   "      essayer d'utiliser le fichier de configuration\n"
		   "      de la distribution officielle de %s [www.ffothello.org/info/appa.php].\n"
		   "      Lisez la documentation pour les details.\n"
#endif
		   , config_filename, config_filename, VERSION);
	/* (void)read_key(); */
}

void warning_old_papp_cfg(void) {
	/* clearScreen(); */
	beep();
	printf("\n"
#ifdef ENGLISH
		   "      WARNING: Your configuration file \"%s\" is the one\n"
		   "      used in Papp up to version 1.29. A new type of\n"
		   "      configuration file is available. I shall use your file\n"
		   "      but you should probably try to use instead the config\n"
		   "      file from the official %s distribution\n"
		   "      [www.ffothello.org/info/appa.php].\n"
#else
		   "      Votre fichier de configuration \"%s\" correspond\n"
		   "      a celui utilise jusqu'a la version 1.29 de Papp.\n"
		   "      Un nouveau type de fichier de configuration est\n"
		   "      maintenant disponible. Je vais utiliser le fichier indique\n"
		   "      mais je vous recommande d'utiliser le fichier de\n"
		   "      configuration de la distribution officielle de %s\n"
		   "      [www.ffothello.org/info/appa.php].\n"
#endif
		   , config_filename, VERSION );
	/* (void)read_key();*/
}

void displayParameterType(void) {
	printf("\n"
#ifdef ENGLISH
		   "      Parameters currently in use, which you may wish \n"
		   "      to change by editing the papp.cfg file.\n\n"
		   "          Default country: %s\n"
		   "          BYE score and total number of discs: %s/%ld\n"
		   "          Use a subfolder: %s\n"
#else
		   "      Parametres actuellement utilises, que vous\n"
		   "      pouvez changer en editant le fichier papp.cfg\n\n"
		   "          Pays par defaut : %s\n"
		   "          Score de BIP et total de pions : %s/%ld\n"
		   "          Utilisation d'un sous-repertoire : %s\n"
#endif
		   , default_country, discs2string(bye_score), discsTotal,
#ifdef ENGLISH
		   (use_subfolder?"YES":"NO"));
#else
	(use_subfolder?"OUI":"NON"));
#endif
	/* (void)read_key(); */
}

void warningTwoPlayersFilesFound(char *PlayersFilenamne,
                                      char *PlayersFilename_Alt) {
	clearScreen();
	beep();
	printf("\n\n"
#ifdef ENGLISH
		   "      PROBLEM : you have two database files of players.\n"
		   "\n"
		   "      There seems to exist, in the current directory,\n"
		   "      both a file named     :  \"%s\" \n"
		   "      and another named     :  \"%s\" \n"
		   "      (Beware of the option in Windows or in Mac OS X\n"
		   "      to hide the file extension, btw).\n\n\n"
		   "      Please delete or rename the obsolete player file,\n"
		   "      then relaunch Papp. Thanks !\n"
#else
		   "      PROBLEM : vous avez deux fichiers de joueurs.\n"
		   "\n"
		   "      Vous semblez avoir, dans le repertoire courant,\n"
		   "      a la fois un fichier    :  \"%s\" \n"
		   "      et un autre fichier     :  \"%s\" \n"
		   "      (Peut-etre est-ce du au fait que Windows ou Mac OS X\n"
		   "      a cache les extensions de fichier, au fait).\n\n\n"
		   "      En tout cas, veuillez detruire ou renommer le fichier\n"
		   "      obsolete, puis relancer Papp. Merci !\n"
#endif
		   , PlayersFilenamne, PlayersFilename_Alt);
	(void) read_key();
}

void displayTournamentBanner(void) {
	printf("\n\n"
#ifdef ENGLISH
		   "   **************************************************************\n"
		   "   *                  Current tournament info                   *\n"
		   "   **************************************************************\n"
#else
		   "   **************************************************************\n"
		   "   *                Informations tournoi en cours               *\n"
		   "   **************************************************************\n"
#endif
		   );
}

/* Si c'est un nouveau tournoi ou un ancien fichier intermediaire
 *  On demande les caracteristiques du tournoi.
 ****
 * If it is a new tournament or an old workfile, the tournament information
 *  are asked.
   */
void askTournamentInformation(long fileType) {
	char *tmp;
	double BQ_tmp ;
	struct tm *t;
    time_t now;
	char date_tmp[25];

	assert(fileType == NONEXISTING || fileType == OLD) ;
	/*	clearScreen(); */
	beep();
	if (fileType == NONEXISTING) {
		printf("\n"
#ifdef ENGLISH
			   "      You are starting a new tournament.\n"
			   "      Please enter its main caracteristics.\n"
#else
			   "      Vous debutez un nouveau tournoi.\n"
			   "      Merci de m'indiquer ses caracteristiques.\n"
#endif
			   ) ;
	} else {
		printf("\n"
#ifdef ENGLISH
			   "      I found an old 'papp-workfile' file used in an old Papp version.\n"
			   "      Please enter the main caracteristics of the tournament.\n"
#else
			   "      J'ai trouve le fichier 'papp-workfile' du tournoi\n"
			   "      correspondant a une ancienne version de Papp.\n"
			   "      Merci de m'indiquer les caracteristiques du tournoi.\n"
#endif
			   ) ;
	}
	printf(MSG_NAME_TRN) ;
	tmp = read_Line() ;
	COPY(tmp, &tournament_name);
	putchar('\n');
	number_of_rounds = 0 ;
	while( (number_of_rounds<1) || (number_of_rounds>NMAX_ROUNDS) ) {
		printf(MSG_NBR_ROUNDS) ;
		if (sscanf(read_Line(), "%ld", &number_of_rounds) != 1) {
			beep() ;
			number_of_rounds = 0 ;
		}
		putchar('\n');
	}
	/* Si 2n-1 ou 2n rondes, on a 2n joueurs max pour un toutes-rondes -
	 * maximum of 2n players for a round-robin if we have 2n or 2n-1 rounds.*/
	maxPlayers4rr = ((number_of_rounds+1)/2)*2 ;
	minPlayers4rr = 1 ;
	printf(MSG_RROBIN, maxPlayers4rr) ;
	brightwell_coeff = (number_of_rounds<8 ? 0.0 : 3.0) ; /* coeff par defaut - default coeff. */
	BQ_tmp = -1.0 ;
	do {
		printf(MSG_BQ, brightwell_coeff*2) ;
		if (sscanf(read_Line(), "%lf", &BQ_tmp) < 1)
			BQ_tmp = brightwell_coeff*2 ;
		putchar('\n');
	} while (BQ_tmp<0) ;
	brightwell_coeff = BQ_tmp/2 ;
/* Quelle date pour le tournoi ? - Which date for the tournament? */
    time(&now);
    t = localtime(&now);
    assert(t != NULL);
    sprintf(date_tmp, "%02d/%02d/%04d",t->tm_mday, t->tm_mon+1, t->tm_year+1900);
	COPY(date_tmp, &tournament_date);

	/*	(void)read_key(); */
}

void displayTournamentParameters(void) {
	printf("\n"
#ifdef ENGLISH
		   "          Name of tournament: %s\n"
		   "          Date of tournament: %s\n"
		   "          Number of rounds: %ld\n"
		   "          Brightwell constant: %f\n"
#else
		   "          Nom du tournoi : %s\n"
		   "          Date du tournoi : %s\n"
		   "          Nombre de rondes : %ld\n"
		   "          Coefficient de Brightwell : %f\n"
#endif
		   , tournament_name, tournament_date, number_of_rounds, brightwell_coeff*2) ;

	screen_bottom();
	reverse_video(PRESS_KEY);
	(void) read_key();
}

static void buildTournamentFolder(const char *name, char *folder) {
	const char *special_chars = "#&%:?*!/''\"\\";
	unsigned long i;

	if (name == NULL || name[0] == '\0') {
		strcpy(folder, "tournament/");
		return;
	}

	strcpy(folder, name);
	for (i = 0; i < strlen(folder); i++) {
		if (folder[i] == ' ')
			folder[i] = '_';
		if (strchr(special_chars, folder[i]))
			folder[i] = '-';
	}
	strcat(folder, "/");
}

static long tournamentFileExists(const char *filename) {
	FILE *fp;

	fp = fopen(filename, "rb");
	if (fp == NULL)
		return 0;
	fclose(fp);
	return 1;
}

static long copyTournamentFile(const char *source, const char *destination) {
	FILE *source_file;
	FILE *destination_file;
	int c;
	long result;

	if (strcmp(source, destination) == 0)
		return 0;

	source_file = fopen(source, "rb");
	if (source_file == NULL)
		return -1;
	destination_file = fopen(destination, "wb");
	if (destination_file == NULL) {
		fclose(source_file);
		return -1;
	}

	result = 0;
	while ((c = fgetc(source_file)) != EOF) {
		if (fputc(c, destination_file) == EOF) {
			result = -1;
			break;
		}
	}
	if (ferror(source_file) || ferror(destination_file))
		result = -1;
	if (fclose(source_file) != 0)
		result = -1;
	if (fclose(destination_file) != 0)
		result = -1;
	return result;
}

static long parseTournamentRecord(char *line, TournamentRecord *record) {
	char *first_separator;
	char *second_separator;

	if (line[0] == '\0' || line[0] == '#')
		return 0;
	first_separator = strchr(line, '|');
	if (first_separator == NULL)
		return 0;
	*first_separator = '\0';
	second_separator = strchr(first_separator + 1, '|');
	if (second_separator == NULL)
		return 0;
	*second_separator = '\0';
	if (line[0] == '\0' || first_separator[1] == '\0')
		return 0;

	strncpy(record->folder, line, sizeof(record->folder) - 1);
	record->folder[sizeof(record->folder) - 1] = '\0';
	strncpy(record->name, first_separator + 1, sizeof(record->name) - 1);
	record->name[sizeof(record->name) - 1] = '\0';
	strncpy(record->date, second_separator + 1, sizeof(record->date) - 1);
	record->date[sizeof(record->date) - 1] = '\0';
	return 1;
}

static long loadTournamentRecords(TournamentRecord records[]) {
	FILE *fp;
	char line[4096];
	char archive_filename[TOURNAMENT_PATH_LENGTH];
	long count;

	fp = fopen(TOURNAMENT_INDEX_FILENAME, "r");
	if (fp == NULL) {
		if (errno == ENOENT)
			return 0;
		return -1;
	}

	count = 0;
	while (fgets(line, sizeof(line), fp) != NULL) {
		line[strcspn(line, "\r\n")] = '\0';
		if (count < TOURNAMENT_RECORDS_MAX &&
			parseTournamentRecord(line, &records[count])) {
			if (strlen(records[count].folder) +
				strlen(ARCHIVED_WORKFILE_FILENAME) >= sizeof(archive_filename))
				continue;
			strcpy(archive_filename, records[count].folder);
			strcat(archive_filename, ARCHIVED_WORKFILE_FILENAME);
			if (tournamentFileExists(archive_filename))
				++count;
		}
	}
	if (fclose(fp) != 0)
		return -1;
	return count;
}

static long saveTournamentRecords(TournamentRecord records[], long count) {
	FILE *fp;
	long i;

	fp = fopen(TOURNAMENT_INDEX_FILENAME, "w");
	if (fp == NULL)
		return -1;
	for (i = 0; i < count; i++)
		if (fprintf(fp, "%s|%s|%s\n", records[i].folder,
				records[i].name, records[i].date) < 0) {
			fclose(fp);
			return -1;
		}
	if (fclose(fp) != 0)
		return -1;
	return 0;
}

static long loadCurrentTournamentFolder(const char *name, const char *date,
										char *folder) {
	FILE *fp;
	char line[4096];
	TournamentRecord record;

	fp = fopen(CURRENT_TOURNAMENT_FILENAME, "r");
	if (fp == NULL)
		return 0;
	if (fgets(line, sizeof(line), fp) == NULL) {
		fclose(fp);
		return 0;
	}
	line[strcspn(line, "\r\n")] = '\0';
	if (fclose(fp) != 0)
		return 0;
	if (!parseTournamentRecord(line, &record))
		return 0;
	if (strcmp(record.name, name) != 0 || strcmp(record.date, date) != 0)
		return 0;
	strcpy(folder, record.folder);
	return 1;
}

static long saveCurrentTournamentFolder(const char *folder) {
	FILE *fp;

	fp = fopen(CURRENT_TOURNAMENT_FILENAME, "w");
	if (fp == NULL)
		return -1;
	if (fprintf(fp, "%s|%s|%s\n", folder, tournament_name,
				tournament_date) < 0) {
		fclose(fp);
		return -1;
	}
	if (fclose(fp) != 0)
		return -1;
	return 0;
}

static long addTournamentRecord(TournamentRecord records[], long *count,
									const char *folder) {
	long i;

	for (i = 0; i < *count; i++)
		if (strcmp(records[i].folder, folder) == 0) {
			strncpy(records[i].name, tournament_name,
					sizeof(records[i].name) - 1);
			records[i].name[sizeof(records[i].name) - 1] = '\0';
			strncpy(records[i].date, tournament_date,
					sizeof(records[i].date) - 1);
			records[i].date[sizeof(records[i].date) - 1] = '\0';
			return 0;
		}

	if (*count >= TOURNAMENT_RECORDS_MAX)
		return -1;
	strncpy(records[*count].folder, folder,
			sizeof(records[*count].folder) - 1);
	records[*count].folder[sizeof(records[*count].folder) - 1] = '\0';
	strncpy(records[*count].name, tournament_name,
			sizeof(records[*count].name) - 1);
	records[*count].name[sizeof(records[*count].name) - 1] = '\0';
	strncpy(records[*count].date, tournament_date,
			sizeof(records[*count].date) - 1);
	records[*count].date[sizeof(records[*count].date) - 1] = '\0';
	++*count;
	return 0;
}

static long archiveCurrentTournament(const char *folder,
									 TournamentRecord records[], long *count) {
	char archive_filename[TOURNAMENT_PATH_LENGTH];

	if (mkdir(folder, 0777) == -1 && errno != EEXIST)
		return -1;
	if (strlen(folder) + strlen(ARCHIVED_WORKFILE_FILENAME) >=
										 sizeof(archive_filename))
		return -1;
	strcpy(archive_filename, folder);
	strcat(archive_filename, ARCHIVED_WORKFILE_FILENAME);
	if (copyTournamentFile(workfile_filename, archive_filename) != 0)
		return -1;
	if (addTournamentRecord(records, count, folder) != 0)
		return -1;
	return saveTournamentRecords(records, *count);
}

static long restoreTournament(const TournamentRecord *record) {
	char archive_filename[TOURNAMENT_PATH_LENGTH];

	if (strlen(record->folder) + strlen(ARCHIVED_WORKFILE_FILENAME) >=
										 sizeof(archive_filename))
		return -1;
	strcpy(archive_filename, record->folder);
	strcat(archive_filename, ARCHIVED_WORKFILE_FILENAME);
	if (!tournamentFileExists(archive_filename))
		return -1;
	return copyTournamentFile(archive_filename, workfile_filename);
}

static long tournamentFolderUsed(const char *folder,
								 const TournamentRecord records[], long count) {
	char archive_filename[TOURNAMENT_PATH_LENGTH];
	long i;

	for (i = 0; i < count; i++)
		if (strcmp(records[i].folder, folder) == 0)
			return 1;
	if (strlen(folder) + strlen(ARCHIVED_WORKFILE_FILENAME) >=
										 sizeof(archive_filename))
		return 1;
	strcpy(archive_filename, folder);
	strcat(archive_filename, ARCHIVED_WORKFILE_FILENAME);
	return tournamentFileExists(archive_filename);
}

static void removeTournamentRecord(TournamentRecord records[], long *count,
									   long index) {
	long i;

	for (i = index; i < *count - 1; i++)
		records[i] = records[i + 1];
	--*count;
}

static long makeNewTournamentFolder(const TournamentRecord records[], long count,
										char *folder) {
	char base[TOURNAMENT_PATH_LENGTH];
	char stem[TOURNAMENT_PATH_LENGTH];
	char candidate[TOURNAMENT_PATH_LENGTH];
	long suffix;

	buildTournamentFolder(tournament_name, base);
	if (!tournamentFolderUsed(base, records, count)) {
		strcpy(folder, base);
		return 0;
	}
	strcpy(stem, base);
	stem[strlen(stem) - 1] = '\0';
	for (suffix = 2; suffix < 10000; suffix++) {
		if (strlen(stem) + 20 >= sizeof(candidate))
			return -1;
		sprintf(candidate, "%s_%ld/", stem, suffix);
		if (!tournamentFolderUsed(candidate, records, count)) {
			strcpy(folder, candidate);
			return 0;
		}
	}
	return -1;
}

static long chooseTournament(TournamentRecord records[], long count) {
	char *line;
	long selection;

	printf(MSG_TOURNAMENT_CHOICE);
	printf(MSG_TOURNAMENT_RESUME);
	printf(MSG_TOURNAMENT_NEW);
	if (count > 0) {
#ifdef ENGLISH
		printf("        Saved tournaments:\n");
#else
		printf("        Tournois sauvegardes :\n");
#endif
		for (selection = 0; selection < count; selection++)
			printf(MSG_TOURNAMENT_RESTORE, selection + 1,
					records[selection].name);
	}
	printf(MSG_TOURNAMENT_CHOICE_PROMPT);
	for (;;) {
		line = read_Line();
		if (tolower((unsigned char)line[0]) == 'r')
			return TOURNAMENT_RESUME;
		if (tolower((unsigned char)line[0]) == 'n')
			return TOURNAMENT_NEW;
		if (sscanf(line, "%ld", &selection) == 1 &&
				selection >= 1 && selection <= count)
			return selection - 1;
		beep();
		printf(MSG_TOURNAMENT_CHOICE_PROMPT);
	}
}

static void resetTournamentState(void) {
	emptyList(registered_players);
	emptyList(new_players);
	emptyList(emigrant_players);
	emptyList(team_captain);
	first_round();
}

void Init_Subfolder(void) {
	char folder[TOURNAMENT_PATH_LENGTH];

	if (use_subfolder != 1)
		return ;
	if (tournament_name == NULL || tournament_name[0] == '\0')
		return ;
	if (subfolder_name == NULL || subfolder_name[0] == '\0') {
		buildTournamentFolder(tournament_name, folder);
		COPY(folder, &subfolder_name);
	}
	/* The folder name was prepared before entering this function. */
	/* Le repertoire existe-t-il ? - directory exists? */

	if (mkdir(subfolder_name, 0777) == -1)
		if (errno != EEXIST)
			/* Le repertoire ne peut etre cree et ce n'est pas parce qu'il existe -
			 * directory cannot by created and it is not because it exists */
			return ;
	/* Modifier le fullname des fichiers - modify other files fullname */
	CONCAT(subfolder_name, pairings_filename, &pairings_filename) ;
	CONCAT(subfolder_name, results_filename, &results_filename) ;
	CONCAT(subfolder_name, standings_filename, &standings_filename) ;
	CONCAT(subfolder_name, team_standings_filename, &team_standings_filename) ;
	CONCAT(subfolder_name, HTML_crosstable_filename, &HTML_crosstable_filename) ;
	CONCAT(subfolder_name, elo_filename, &elo_filename) ;
	/* Pour le fichier intermediaire, il faut garder les deux noms : l'original et celui
	 * concatene avec le sous-dossier pour la sauvegarde
	 ****
	 * For the workfile, both fullnames must be kept: the original one
	 *  and the one concatenated with the subfolder for the backup.
	 */
	CONCAT(subfolder_name, workfile_filename, &backup_workfile_filename) ;
	CONCAT(backup_workfile_filename, "-BACKUP", &backup_workfile_filename) ;
}

int main (int argc, char **argv) {
	long ret;
	char PlayersFilename_Alt[2048];
	char current_folder[TOURNAMENT_PATH_LENGTH];
	char selected_folder[TOURNAMENT_PATH_LENGTH];
	long saved_tournament_count;
	long tournament_choice;
	TournamentRecord saved_tournaments[TOURNAMENT_RECORDS_MAX];
	if (argc == 2 && strcmp(argv[1], "--tournament-json") == 0) {
		program_name = argv[0];
		return papp_tournament_json_main();
	}

 #if defined(PAPP_MAC_METROWERKS)
	init_mac_SIOUX_console();
	program_name = VERSION;
 #else
	program_name = argv[0];
 #endif

	/*
	 * L'alloc-ring est utilise pour (quasiment) toutes les allocations
	 * temporaires de chaines, mais il s'initialise tout seul !
	 ****
	 * Alloc-ring is used for nearly all temporary string allocations
	 * but it is self-initialized!
	 */


	/* Initialisation de l'ecran, puis du clavier - Screen and keyboard initialize */
	init_screen();
	init_keyboard();

	/* Message de presentation/copyright - presentation/copyright message */
	clearScreen() ;
	printf(MSG_WELCOME, VERSION, __DATE__);


	/* Initialisation des listes de joueurs - list of players initialization */
	registered_players   = createList();
	new_players   = createList();
	emigrant_players    = createList();
	team_captain = createList();

	/* Initialisation de l'historique - history initialization */
	first_round();
	/* Initialisation des tableaux de penalites - penalties array initialization */
    init_default_penalties();

	/* Recherche du fichier de configuration - look for configuration file */
	if (getenv("PAPP_CFG"))
		config_filename = getenv("PAPP_CFG");
	if (argc > 2)
		fatal_error("usage: papp [config-file]");
	else if (argc == 2)
		config_filename = argv[1];

	/* Lecture du fichier de config - reading configuration file */
	config_file_type = NONEXISTING ;
	displayConfigBanner() ;
	ret = read_file(config_filename, CONFIG_F);
	if (ret > 0)
		fatal_error(BAD_CONFIG);
	if (ret == -1 && errno == ENOENT) {
		/* Sauvegarde de la configuration par defaut, si possible - Saving default configuration, if possible */
		FILE *fp;
		if ((fp = myfopen_in_subfolder(config_filename, "w", "", 0, 0)) != NULL) {
			WarningNoConfigFile();
			block_signals();
            big_dump(fp);
			unblock_signals();
			fclose(fp);
		}
	}
	if (ret == 0 && config_file_type == OLD) {
		/* Prevenir l'utilisateur que c'est un ancien type de fichier cfg -
		 * Warn user it is an old format type for configuration file */
		warning_old_papp_cfg() ;
	}
	displayParameterType() ;

	/* Fabrication du fullname de fichier "joueurs.txt" - "joueurs.txt" fullname construction */
	(void)strcpy(PlayersFilename_Alt, players_filename);
	(void)strcat(PlayersFilename_Alt, ".txt");

	/* Verifions que l'utilisateur ne s'est pas emmele les pinceaux
	   avec les fichiers joueurs et joueurs.txt -
	   Check user didn't mix "joueurs" and "joueurs.txt"*/
	if (isFIleExist(players_filename) &&
        isFIleExist(PlayersFilename_Alt))
	    {
			warningTwoPlayersFilesFound(players_filename, PlayersFilename_Alt);
			terminate();
	    return (-999);
	    }

	/* Lecture du fichier des joueurs, sans l'extension .txt - reading players file without extension .txt */
	ret = read_file(players_filename, PLAYERS_F);
	if (ret > 0)
		fatal_error(BAD_PLAYERS);

	/* Lecture du fichier des joueurs, avec l'extension .txt - reading players file with extension .txt */
	ret = read_file(PlayersFilename_Alt, PLAYERS_F);
	if (ret > 0)
		fatal_error(BAD_PLAYERS);

	/* Lecture des nouveaux - reading new players file */
	ret = read_file(new_players_filename, NEWPLAYERS_F);
	if (ret > 0)
		fatal_error(BAD_NEWP);

	/* Si le fullname du fichier intermediaire est different de '__papp__',
	   on renomme un ancien fichier '__papp__' avec ce nouveau fullname.
	   - if fullname of workfile is different from '__papp__',
	   an old '__papp__' file is renamed with that new fullname. */
	if (strcmp(workfile_filename, "__papp__")) {
		if (isFIleExist("__papp__") && !isFIleExist(workfile_filename)) {
			rename("__papp__", workfile_filename);
		}
	}
	/* Nous pouvons maintenant lire le fichier de sauvegarde - we can now read saved workfile */
	workfile_type = NONEXISTING ;
	displayTournamentBanner() ;
	ret = read_file(workfile_filename, CONFIG_F);
	if (ret > 0)
		fatal_error(BAD_SAVE);

	saved_tournament_count = loadTournamentRecords(saved_tournaments);
	if (saved_tournament_count < 0)
		fatal_error(MSG_TOURNAMENT_INDEX_ERROR);

	if (workfile_type != CORRECT) {
		/* On a soit un vieux fichier intermediaire, soit pas de fichier -
		 * We either have an old workfile or no file at all. */
		askTournamentInformation(workfile_type) ;
		init_workfile(workfile_type) ;
		if (makeNewTournamentFolder(saved_tournaments, saved_tournament_count,
									current_folder) != 0)
			fatal_error(MSG_TOURNAMENT_INDEX_WRITE_ERROR);
		COPY(current_folder, &subfolder_name);
		if (saveCurrentTournamentFolder(current_folder) != 0)
			fatal_error(MSG_TOURNAMENT_INDEX_WRITE_ERROR);
	} else {
		displayTournamentParameters() ;
		if (!loadCurrentTournamentFolder(tournament_name, tournament_date,
									 current_folder))
			buildTournamentFolder(tournament_name, current_folder);

		tournament_choice = chooseTournament(saved_tournaments,
										 saved_tournament_count);
		if (tournament_choice == TOURNAMENT_NEW) {
			if (archiveCurrentTournament(current_folder, saved_tournaments,
										  &saved_tournament_count) != 0)
				fatal_error(MSG_TOURNAMENT_ARCHIVE_ERROR);
			resetTournamentState();
			askTournamentInformation(NONEXISTING);
			init_workfile(NONEXISTING);
			if (makeNewTournamentFolder(saved_tournaments, saved_tournament_count,
										current_folder) != 0)
				fatal_error(MSG_TOURNAMENT_INDEX_WRITE_ERROR);
			COPY(current_folder, &subfolder_name);
			if (saveCurrentTournamentFolder(current_folder) != 0)
				fatal_error(MSG_TOURNAMENT_INDEX_WRITE_ERROR);
		} else if (tournament_choice >= 0) {
			strcpy(selected_folder,
					saved_tournaments[tournament_choice].folder);
			if (archiveCurrentTournament(current_folder, saved_tournaments,
										  &saved_tournament_count) != 0)
				fatal_error(MSG_TOURNAMENT_ARCHIVE_ERROR);
			if (restoreTournament(&saved_tournaments[tournament_choice]) != 0)
				fatal_error(MSG_TOURNAMENT_RESTORE_ERROR);
			removeTournamentRecord(saved_tournaments, &saved_tournament_count,
										 tournament_choice);
			if (saveTournamentRecords(saved_tournaments,
										 saved_tournament_count) != 0)
				fatal_error(MSG_TOURNAMENT_INDEX_WRITE_ERROR);

			resetTournamentState();
			workfile_type = NONEXISTING;
			ret = read_file(workfile_filename, CONFIG_F);
			if (ret > 0 || workfile_type != CORRECT)
				fatal_error(BAD_SAVE);
			COPY(selected_folder, &subfolder_name);
			if (saveCurrentTournamentFolder(selected_folder) != 0)
				fatal_error(MSG_TOURNAMENT_INDEX_WRITE_ERROR);
		} else {
			COPY(current_folder, &subfolder_name);
			if (saveCurrentTournamentFolder(current_folder) != 0)
				fatal_error(MSG_TOURNAMENT_INDEX_WRITE_ERROR);
		}
	}

	/* Creation eventuelle d'un sous-dossier et modification des noms de fichiers -
	 * optionnal creation of subfolder and change of fullnames. */
	if (use_subfolder == 1) {
        Init_Subfolder() ;
	}
	/* Pour eviter une sauvegarde automatique - to prevent automatic saves */
	do_not_save_registered();
	do_not_save_pairings();

	install_signals();
	check_penalties();

    /* afficher les infos - display informations */
    /*
    messages_copyleft();
    (void)read_key();
    */

	/* Boucle principale - main loop */
	main_menu();

	return (-999);
}

#ifdef ENGLISH
# define WHICH_FILENAME		"Filename?"
# define WHICH_PLAYER		"Which player?"
# define WHICH_FEATS		"Feats for which player?"
# define WHO_COMES			"Who comes back?"
# define WHO_LEAVES			"Who leaves?"
# define OLD_NATION         "Old nationality: "
# define NEW_NATION         "New nationality: "
#else
# define WHICH_FILENAME		"Nom de fichier ?"
# define WHICH_PLAYER		"Quel joueur ?"
# define WHICH_FEATS		"Fiche de quel joueur ?"
# define WHO_COMES			"Qui revient ?"
# define WHO_LEAVES			"Qui s'en va ?"
# define OLD_NATION         "Ancienne nationalite: "
# define NEW_NATION         "Nouvelle nationalite: "
#endif


#define ASK_FILENAME(var)				\
	printf("\n\n%s ", WHICH_FILENAME);		\
	var = strcpy(new_string(), read_Line());	\
	putchar('\n'); if (*var == '\0')  break;

#define ASK_PLAYER(var)					\
	printf("\n\n%s ", WHICH_PLAYER);		\
	if (sscanf(read_Line(), "%ld", &var) != 1)	\
		break;

static void main_menu (void) {
    char c, *line, *file;
    long playerID, k;
#ifdef atarist
    extern int __mint;
#endif

    for(;;) {
		init_screen();
		clearScreen();
#ifdef ENGLISH
        printf("\n                MAIN MENU\n\n"
        "        & About this program\n"
        "        I Inscription of new players\n"
        "        L List of all players\n"
        "        - Someone quits the tournament\n"
        "        + Someone enters the tournament\n"
        "        N Nationality of a player\n"
        "        F Feats of a player\n"
        "        T Team results\n"
		);
		printf(
		"        W Crosstable and Web page export\n"
        "        M Manual pairings\n"
        "        A Automatic pairings\n"
        "        V View pairings\n"
        "        R Results of this round\n"
        "        C Correct an old result\n"
        "        E Create an ELO file\n"
        "        S Save the current ranking\n"
        "        X Exit this program\n"
        "\n        Your choice? ");
#else
        printf("\n                MENU PRINCIPAL\n\n"
        "        & Version du programme\n"
        "        I Inscription des joueurs\n"
        "        L Liste des joueurs\n"
        "        - Depart d'un joueur\n"
        "        + Retour d'un joueur\n"
        "        N Nationalite des joueurs\n"
        "        F Fiche individuelle d'un joueur\n"
        "        T Totaux des equipes\n"
		);
		printf(
        "        W Tableaux des resultats et page Web\n"
        "        M Appariement manuel\n"
        "        A Appariement automatique\n"
        "        V Voir les appariements\n"
        "        R Resultats d'une ronde\n"
        "        C Corriger un vieux resultat\n"
        "        E Creation d'un fichier ELO\n"
        "        S Sauvegarder le classement\n"
        "        X Quitter le programme\n"
        "\n        Votre choix ? ");
#endif

lire:
        c = read_key();
        c = tolower(c);
        file = NULL;
		init_screen();

    /* sur Mac OS X, les touches speciales F1, .., F14 et les fleches (bas, haut, etc.)
     * envoient deux caracteres ascii: on  appelle deux fois de plus lire_touche() pour
     * consommer les caracteres parasites
     *****
     * On MacOS X, special keys F1,...,F14 and arrows send two ascii characters;
     * read_key() is called twice more to consume extra characters. */
#if defined(MACOSX)
        if (c == '\033') {read_key();read_key();c='\n';}
#endif

        switch (c) {
	        case '&':
			    messages_copyleft();
		    touche: (void) read_key();
			    break;
	        case 'i':
                enter_players();
			    break;
	        case 's':
			    ASK_FILENAME(file);
	        case 'l':
			    display_registered(file);
			    break;
		    case 't':
				display_teams(file);
			    break;
	        case '\026':	/* Ctrl-V */
			    ASK_FILENAME(file);
	        case 'v':
				display_pairings(file, 0);
			    break;
	        case '\006':	/* Ctrl-F */
			    ASK_FILENAME(file);
	        case 'f':
				clearScreen();
			    printf("\n\n");
			    if ((playerID = selectPlayerFromKeyboard(WHICH_FEATS, registered_players, &line)) <= 0)
				    break;
                individualSheet(playerID, file);
			    break;
#if defined(UNIX_BSD) || defined(UNIX_SYSV)
	        case '\032':	/* Ctrl-Z */
# ifdef atarist
			    if (__mint > 0)
# endif
		        {
				screen_bottom();
				clearScreen();
				fflush(stdout);
				keyboard_reset();
				screen_reset();
				goodbye();
				raise(SIGTSTP);  /* STOP */
				init_screen();
				init_keyboard();
		        }
			    break;
#endif
	        case 'x':
				terminate();
			    break;
	        case '+':
				clearScreen();
			    playerID = selectPlayerFromKeyboard(WHO_COMES, registered_players, &line);
			    if (playerID == ALL_PLAYERS) {
				    for (k = 0; k < registered_players->n; k++)
                        player_InOut(registered_players->list[k]->ID, 1);
				    goto touche; /* Laisser les messages a l'ecran - leave messages on screen */
			    }
				else if (playerID >= 0) {player_InOut(playerID, 1); goto touche; }
				    break;
	        case '-':
				clearScreen();
			    playerID = selectPlayerFromKeyboard(WHO_LEAVES, registered_players, &line);
			    if (playerID == ALL_PLAYERS) {
				    for (k = 0; k < registered_players->n; k++)
                        player_InOut(registered_players->list[k]->ID, 0);
				    goto touche; /* Laisser les messages a l'ecran - leave messages on screen */
			    }
				else if (playerID >= 0) {player_InOut(playerID, 0); goto touche; }
				    break;
	        case 'n':
			    printf("\n\n");
			    if ((playerID = selectPlayerFromKeyboard(WHICH_PLAYER, NULL, &line)) <= 0)
				    break;
				{
				Player *j = findPlayer(playerID);
				if (j == NULL)
					beep();
				else {
					clear_line();
					printf("%s (%ld)\n", j->fullname, j->ID);
					printf( OLD_NATION "%s\n", j->country);
					printf( NEW_NATION);
					line = read_Line();
					if (line[0]) {
						change_nationality(playerID, line);
						addPlayer(emigrant_players, j);
					}
				}
				}
				break;
	        case 'r':
				enterResults();
			    break;
		    case 'c':
                ResultCorrection();
			    break;
		    case 'w':
			    if (!CanAllocateCrosstableMemory()) {
					clearScreen();
				    printf(MSG_CROSS_MEMORY);
				    beep();
				    goto touche;
			    } else {
                    DisplayCrosstable();
					FreeCrosstableMemory();
			    }
			    break;
	        case 'a':
                compute_pairings();
			    break;
		    case 'm':
				pairings_manipulate();
			    break;
	        case 'e':
                create_ELO_file();
			    break;
	        case '\n':		/* Ctrl-J */
	        case '\r':		/* Ctrl-M */
	        case '\f':		/* Ctrl-L */
	        case '\022':	/* Ctrl-R */
			    break;  /* redessiner l'ecran - redraw screen */
	        default:
			    beep();
			    goto lire;
	    }       /* switch */
    }           /* for(;;) */
}
