/*
 * edtest.c: test de l'algorithme d'Edmonds, sur un certain
 * nombre d'exemples tires au hasard.
 */

static char rcsid[] = "$Id: edtest.c,v 1.5 1994/08/06 17:01:31 bousch Exp $";

#include <stdio.h>
#include <stdlib.h>
#include <assert.h>
#include "appari.h"

#define  PEN__MIN   1000
#define  PEN__MAX   9999999
#define  MODULO     (PEN__MAX - PEN__MIN + 1)
#define  FACTEUR    29

typedef struct _test {
    int n;      /* moitie du nombre de joueurs */
    long seed;  /* semence pour les nombres aleatoires */
    long penopt;    /* penalite optimum attendue */
} TEST;

TEST liste_tests[] = {
    {   2,   746,   7409781 },
    {   3,   901,   4146783 },
    {   4,   174,   4315332 },
    {   5,   345,   3920857 },
    {   6,   800,   2976723 },
    {   7,   888,   4055646 },
    {   8,  1064,   2013084 },
    {   9,   251,   3104415 },
    {  10,  1700,   4169196 },
    {  12,   100,   3889348 },
    {  14,   268,   3981382 },
    {  16,   333,   4312003 },
    {  18,   499,   3651602 },
    {  20,   637,   5892467 },
    {  24,  1020,   6794951 },
    {  28,   481,   4440383 },
    {  32,   222,   4124030 },
    {  36,   481,   7243040 },
    {  40,   555,   7415313 },
    {  44,  2022,   3560601 },
    {  48,   111,   6667156 },
    {  52,   101,   3238530 },
    {  56,   347,   4360617 },
    {  60,   412,   31530977 },
    {  65,  1616,   6890250 },
    {  70,   278,   17037360 },
    {  75,   712,   52161646 },
    {  80,   321,   14769870 },
    {  90,  1010,   34344612 },
    { 100,  4752,   88918470 }
};

#define  NOMBRE_TESTS   (sizeof(liste_tests)/sizeof(TEST))

long semence;

int main (void) {
    int sur_opt = 0, sous_opt = 0;
    int i, j, k, nb;
    TEST *t;
    pen_t **p, total;
    int *solution;

    for (k = 0; k < NOMBRE_TESTS; k++) {
        t = liste_tests + k;
        semence = t->seed;
        nb = 2 * t->n;
        p = (pen_t **)malloc(nb*sizeof(pen_t*));
        assert(p);
        for (i = 0; i < nb; i++) {
            p[i] = (pen_t *)malloc(nb*sizeof(pen_t));
            assert(p[i]);
            for (j = 0; j < nb; j++) {
                semence = (1 + semence * FACTEUR) % MODULO;
                p[i][j] = PEN__MIN + semence;
            }
        }
        solution = (int *)malloc(nb*sizeof(int));
        assert(solution);
        printf("Test %2d: %3d joueurs, penalite attendue %ld\n",
            k+1, nb, t->penopt);
        total = apparie(nb/2, p, solution);
        printf("Resultat: %12ld ", total);
        if (total == t->penopt) {
            printf("(ok)\n");
        } else if (total > t->penopt) {
            printf("(Sous-Optimal)\n");
            ++sous_opt;
        } else {
            printf("(SUR-OPTIMAL????)\n");
            ++sur_opt;
        }
        /* Liberer la memoire */
        free(solution);
        for (i = 0; i < nb; i++)
            free(p[i]);
        free(p);
    }
    if (sur_opt == 0 && sous_opt == 0) {
        printf("\nLes %d tests ont tous reussi\n",
            NOMBRE_TESTS);
        return 0;
    }
    printf("\nParmi les %d tests, %d ont echoue:\n"
        "%3d appariements sous-optimaux, et\n"
        "%3d appariements sur-optimaux.\n",
        NOMBRE_TESTS, sous_opt+sur_opt, sous_opt, sur_opt);
    return sous_opt + sur_opt;
}

