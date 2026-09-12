/*
 * xml.c: pour sauvegarder les appariements/resultats et le classement au format xml
 *
 * (EL) 21/11/2020 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, correction d'un bug dans la taille du tableau local
 *                      gardant le nom complet du fichier.
 * (EL) 12/09/2012 : v1.35, no change.
 * (EL) 16/07/2012 : v1.34, Creation du fichier
 *
 *****
 *
 * xml.c: to save pairings/results and standings in xml format.
 *
 * (EL) 21/11/2020 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, bug correction involving the size of a local array
 *                      storing file fullname.
 * (EL) 12/09/2012 : v1.35, no change.
 * (EL) 16/07/2012 : v1.34, Creation of this file
 */



#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include <time.h>
#include <ctype.h>
#include "couplage.h"
#include "global.h"
#include "version.h"
#include "more.h"
#include "discs.h"
#include "player.h"

/* prototype */
void saveCreatorAndTime(FILE *) ;


#ifdef ENGLISH
    #define XML_PROMPT_1       "Creating XML standings file"
    #define XML_PROMPT_2       "Creating XML round file"
    #define CANT_OPEN          "Can't open file"
#else
    #define XML_PROMPT_1       "Creation d'un fichier XML de classement "
    #define XML_PROMPT_2       "Creation d'un fichier XML de rondes "
    #define CANT_OPEN          "Impossible d'ouvrir"
#endif


void saveCreatorAndTime(FILE *fp) {
    struct tm *t;
    time_t now;

    /* Le createur et la date de creation - creator and creation date */
    fprintf(fp, "<!-- Creator: %s -->\n", VERSION);

    time(&now);
    t = localtime(&now);
    assert(t != NULL);
    fprintf(fp, "<!-- CreationDate: %04d/%02d/%02d %02d:%02d -->\n\n",
            t->tm_year+1900, t->tm_mon+1, t->tm_mday,
            t->tm_hour, t->tm_min);
}

void createXMLstandings(void) {
    char filename[1024];
    char str[10] = "";
    FILE *fp;
    long _i, i, nbi;
    long place = 0, lastScore = -1;

    strcpy(filename, subfolder_name);
    strcat(filename, "stand");
    if (current_round < 10) {
        sprintf(str, "__%ld.xml", current_round);
        strcat(filename, str);
    } else if (current_round < 100) {
        sprintf(str, "_%ld.xml", current_round);
        strcat(filename, str);
    } else {
        sprintf(str, "%ld.xml", current_round);
        strcat(filename, str);
    }

    clearScreen();

    printf(XML_PROMPT_1 ": %s\n\n", filename);
    block_signals();
    fp = myfopen_in_subfolder(filename, "w", subfolder_name, use_subfolder, 0);
    if (fp == NULL) {
        printf(CANT_OPEN " '%s'\n",filename);
        goto theEnd;
    }

/* Debut du fichier XML - start of XML file */

    fprintf(fp,
            "<?xml version='1.0' encoding='UTF-8'?>\n"
            "<!DOCTYPE Tournament [\n"
            "<!ELEMENT Tournament (Name,Standing)>\n"
            "<!ELEMENT Standing (Number, Players)>\n"
            "<!ELEMENT Players (Player*)>\n"
            "<!ELEMENT Player (HasLeftTournament?, Place, Points, TieBreak?, Name, Id?, Country?)>\n"
            "<!ELEMENT HasLeftTournament EMPTY>\n"
            "<!ELEMENT Name (#PCDATA)>\n"
            "<!ELEMENT Number (#PCDATA)>\n"
            "<!ELEMENT Place (#PCDATA)>\n"
            "<!ELEMENT Points (#PCDATA)>\n"
            "<!ELEMENT TieBreak (#PCDATA)>\n"
            "<!ELEMENT Id (#PCDATA)>\n"
            "<!ELEMENT Country (#PCDATA)>\n"
        "]>\n\n\n");

    saveCreatorAndTime(fp);

    fprintf(fp,"<Tournament>\n");
    /* Pour sauver le fullname du tournoi en tete - save tournament fullname at start of file */
    fprintf(fp, "\t<Name>%s</Name>\n", tournament_name);
    fprintf(fp, "\t<Standing>\n");
    fprintf(fp, "\t\t<Number>%ld</Number>\n", current_round);
    fprintf(fp, "\t\t<Players>\n");

    nbi = registered_players->n;
    tieBreak_computation();

    if (nbi > 0) {
        long table[MAX_REGISTERED];
        Player *j;

        for (i=0; i<nbi; i++)
            table[i] = i;
        SORT(table, nbi, sizeof(long), sort_players);

        for (i=0; i<nbi; i++) {
            _i = table[i];
            assert(_i >= 0 && _i < nbi);
            j  = registered_players->list[_i];

            fprintf(fp, "\t\t\t<Player>\n");
            if (!present[_i]) {
                fprintf(fp, "\t\t\t\t<HasLeftTournament />\n");
            }

            if (lastScore != score[_i]) {
                lastScore = score[_i];
                place = i+1;
            }
            fprintf(fp, "\t\t\t\t<Place>%ld</Place>\n", place);
            if ((score[_i] % 2) == 1) {
                fprintf(fp, "\t\t\t\t<Points>%ld.5</Points>\n", score[_i] / 2);
            } else {
                fprintf(fp, "\t\t\t\t<Points>%ld</Points>\n", score[_i] / 2);
            }
            fprintf(fp, "\t\t\t\t<TieBreak>%s</TieBreak>\n", tieBreak2string(tieBreak[_i]));
            fprintf(fp, "\t\t\t\t<Name>%s</Name>\n", j->fullname);
            fprintf(fp, "\t\t\t\t<Id>%ld</Id>\n", j->ID);
            fprintf(fp, "\t\t\t\t<Country>%s</Country>\n", j->country);
            fprintf(fp, "\t\t\t</Player>\n");
        }
    }
    fprintf(fp, "\t\t</Players>\n");
    fprintf(fp, "\t</Standing>\n");
    fprintf(fp,"</Tournament>\n");
    fclose(fp);
    theEnd:
    unblock_signals();
/*    (void)read_key(); */
}

void createXMLround(void) {
    char filename[1024];
    FILE *fp;
    Pair *list;
    long nb_pairs, iterateRounds;
    long i, n1, n2, atLeastOneNotPaired;
    discs_t v;
    Player *j;
    if (registered_players->n == 0) {
        return;
    }

    strcpy(filename, subfolder_name);
/*  strcat(filename, "round");

    if (ronde < 10) {
        sprintf(str, "__%ld.xml", ronde+1);
        strcat(filename, str);
    } else if (ronde < 100) {
        sprintf(str, "_%ld.xml", ronde+1);
        strcat(filename, str);
    } else {
        sprintf(str, "%ld.xml", ronde+1);
        strcat(filename, str);
    }
 */
    strcat(filename, "rounds.xml");

    clearScreen();

    printf(XML_PROMPT_2 ": %s\n\n", filename);
    block_signals();
    fp = myfopen_in_subfolder(filename, "w", subfolder_name, use_subfolder, 0);
    if (fp == NULL) {
        printf(CANT_OPEN " '%s'\n",filename);
        goto theEnd_2;
    }

/* Debut du fichier XML - start of XML file */

    fprintf(fp,
            "<?xml version='1.0' encoding='UTF-8'?>\n"
            "<!DOCTYPE Tournament [\n"
            "<!ELEMENT Tournament (Name,Rounds)>\n"
            "<!ELEMENT Rounds (Round*)>\n\n"
            "<!ELEMENT Round (Number, Pairs, NotPairedPlayers)>\n\n"
            "<!ELEMENT Pairs (Pair*)>\n"
            "<!ELEMENT NotPairedPlayers (NotPairedPlayer*)>\n\n"
            "<!ELEMENT Pair (Table?, BlackPlayer, WhitePlayer)>\n\n"
            "<!ELEMENT BlackPlayer (Name, Id?, Score?)>\n"
            "<!ELEMENT WhitePlayer (Name, Id?, Score?)>\n\n"
           );
    fprintf(fp,
            "<!ELEMENT NotPairedPlayer (Name, Id?)>\n\n"
            "<!ELEMENT Name (#PCDATA)>\n"
            "<!ELEMENT Number (#PCDATA)>\n\n"
            "<!ELEMENT Table (#PCDATA)>\n"
            "<!ELEMENT Id (#PCDATA)>\n"
            "<!ELEMENT Score (#PCDATA)>\n"
        "]>\n\n\n");

    saveCreatorAndTime(fp);

    fprintf(fp,"<Tournament>\n");
    /* Pour sauver le fullname du tournoi en tete - save tournament fullname at start of file */
    fprintf(fp, "\t<Name>%s</Name>\n", tournament_name);
    fprintf(fp, "\t<Rounds>\n");

    for (iterateRounds = 0; iterateRounds < current_round+1; iterateRounds++) {
        fprintf(fp, "\t\t<Round>\n");
        fprintf(fp, "\t\t\t<Number>%ld</Number>\n", iterateRounds+1);

        round_iterate(iterateRounds); /* Aller a la bonne ronde */

        /* D'abord afficher les joueurs apparies - First display paired players */
        CALLOC(list, 1 + registered_players->n / 2, Pair);
        nb_pairs = 0;

        while (next_couple(&n1, &n2, &v)) {
            i = nb_pairs++;
            list[i].j1 = findPlayer(n1);
            list[i].j2 = findPlayer(n2);
            list[i].score = v;
            list[i].sort_value = tables_sort_criteria(n1, n2);
        }
        if (iterateRounds >= 1)
            SORT(list, nb_pairs, sizeof(Pair), pairs_sort);

        fprintf(fp,"\t\t\t<Pairs>\n");
        for (i = 0; i < nb_pairs; i++) {
            fprintf(fp, "\t\t\t\t<Pair>\n");

            fprintf(fp, "\t\t\t\t\t<Table>%ld</Table>\n", i+1);

            fprintf(fp, "\t\t\t\t\t<BlackPlayer>\n");
            fprintf(fp, "\t\t\t\t\t\t<Name>%s</Name>\n", list[i].j1->fullname);
            fprintf(fp, "\t\t\t\t\t\t<Id>%ld</Id>\n", list[i].j1->ID);
            if (SCORE_IS_LEGAL(list[i].score)) {
                fprintf(fp, "\t\t\t\t\t\t<Score>%s</Score>\n", discs2string(list[i].score));
            }
            fprintf(fp, "\t\t\t\t\t</BlackPlayer>\n");

            fprintf(fp, "\t\t\t\t\t<WhitePlayer>\n");
            fprintf(fp, "\t\t\t\t\t\t<Name>%s</Name>\n", list[i].j2->fullname);
            fprintf(fp, "\t\t\t\t\t\t<Id>%ld</Id>\n", list[i].j2->ID);
            if (SCORE_IS_LEGAL(list[i].score)) {
                fprintf(fp, "\t\t\t\t\t\t<Score>%s</Score>\n", discs2string(OPPONENT_SCORE(list[i].score)));
            }
            fprintf(fp,"\t\t\t\t\t</WhitePlayer>\n");

            fprintf(fp,"\t\t\t\t</Pair>\n");
        }
        free(list);

        fprintf(fp,"\t\t\t</Pairs>\n");

        /* Puis les joueurs non apparies - Then unpaired players */

        atLeastOneNotPaired = 0;

        for (i = 0; i < registered_players->n; i++) {
            j = (registered_players->list)[i];
            if ((polar2(j->ID, iterateRounds) == 0) && (iterateRounds == current_round ? present[j->ID] : player_was_present(j->ID, iterateRounds))) {
                if (atLeastOneNotPaired == 0) {
                    atLeastOneNotPaired = 1;
                    fprintf(fp,"\t\t\t<NotPairedPlayers>\n");
                }
                fprintf(fp, "\t\t\t\t<NotPairedPlayer>\n");
                fprintf(fp, "\t\t\t\t\t<Name>%s</Name>\n", j->fullname);
                fprintf(fp, "\t\t\t\t\t<Id>%ld</Id>\n", j->ID);
                fprintf(fp, "\t\t\t\t</NotPairedPlayer>\n");
            }
        }
        if (atLeastOneNotPaired == 1) {
            fprintf(fp,"\t\t\t</NotPairedPlayers>\n");
        } else {
            fprintf(fp,"\t\t\t<NotPairedPlayers />\n");
        }
        fprintf(fp,"\t\t</Round>\n");
    }
    fprintf(fp,"\t</Rounds>\n");
    fprintf(fp,"</Tournament>\n");

    fclose(fp);
    theEnd_2:
    unblock_signals();
/*    (void)read_key(); */
}
