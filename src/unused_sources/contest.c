/*
 * contest.c: analogue a edtest, mais fait les tests en continu,
 * en verifiant simplement que `edmonds' ne plante pas. Les resultats
 * sont utilisables pour verification sur d'autres architectures
 * avec edtest:
 *
 *  contest [nombre] >contest.out 2>/dev/null &
 *
 * N'oubliez pas de killer le processus quelques jours plus tard...
 * L'argument `nombre' indique le nombre de tests pour chaque taille de
 * matrice. Si cet argument est omis, ce nombre vaut 10.
 */

static char rcsid[] = "$Id: contest.c,v 1.2 1994/11/20 19:07:21 bousch Exp $";

#include <stdio.h>
#include <stdlib.h>
#include <assert.h>
#include "appari.h"

#define  PEN__MIN   1000
#define  PEN__MAX   9999999
#define  MODULO     (PEN__MAX - PEN__MIN + 1)
#define  FACTEUR    29

void contest (int n, long semence) {
    int i, j, nb;
    pen_t **p, total;
    int *solution;

    printf("\t{%4d,%6ld,", n, semence);
    fflush(stdout);
    nb = 2*n;
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
    total = apparie(n, p, solution);
    printf("\t%ld },\n", total);
    fflush(stdout);
    /* Liberer la memoire */
    free(solution);
    for (i = 0; i < nb; i++)
        free(p[i]);
    free(p);
}

int main (int argc, char **argv) {
    int i, sem, nombre;

    if (argc != 2 || (nombre = atoi(argv[1])) < 1)
        nombre = 10;
    for (i = 1; ; i++)
        for (sem = i; sem < i+nombre; sem++)
            contest(i, sem);
}

