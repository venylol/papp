#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <errno.h>
#include <limits.h>
#include <float.h>
#include <math.h>
#include <time.h>
#if defined(_WIN32)
#include <windows.h>
#include <io.h>
#else
#include <unistd.h>
#endif

#include "global.h"
#include "player.h"
#include "couplage.h"
#include "tournament_json.h"

#define JSON_INPUT_LIMIT (32L * 1024L * 1024L)
#define TOURNAMENT_MAX_PLAYERS MAX_REGISTERED

enum JsonType {
    JSON_NULL,
    JSON_BOOLEAN,
    JSON_NUMBER,
    JSON_STRING,
    JSON_ARRAY,
    JSON_OBJECT
};

typedef struct JsonValue JsonValue;
typedef struct JsonMember JsonMember;

struct JsonMember {
    char *key;
    JsonValue *value;
};

struct JsonValue {
    enum JsonType type;
    char *text;
    JsonValue **items;
    long item_count;
    long item_capacity;
    JsonMember *members;
    long member_count;
    long member_capacity;
};

typedef struct {
    const char *input;
    size_t length;
    size_t position;
    const char *error;
} JsonParser;

typedef struct {
    const char *id;
    const char *name;
    const char *account;
    long papp_id;
} TournamentPlayer;

static TournamentPlayer tournament_players[TOURNAMENT_MAX_PLAYERS];
static long tournament_player_count;
static char tournament_error[256];

static char *copy_range(const char *source, size_t length) {
    char *result;
    result = (char *)malloc(length + 1);
    if (result == NULL) return NULL;
    memcpy(result, source, length);
    result[length] = '\0';
    return result;
}

static JsonValue *new_json_value(enum JsonType type) {
    JsonValue *value;
    value = (JsonValue *)calloc(1, sizeof(JsonValue));
    if (value != NULL) value->type = type;
    return value;
}

static void parser_space(JsonParser *parser) {
    while (parser->position < parser->length &&
           (parser->input[parser->position] == ' ' ||
            parser->input[parser->position] == '\t' ||
            parser->input[parser->position] == '\r' ||
            parser->input[parser->position] == '\n'))
        parser->position++;
}

static int hex_digit(char value) {
    if (value >= '0' && value <= '9') return value - '0';
    if (value >= 'a' && value <= 'f') return value - 'a' + 10;
    if (value >= 'A' && value <= 'F') return value - 'A' + 10;
    return -1;
}

static void append_utf8(char *output, size_t *used, unsigned long codepoint) {
    if (codepoint <= 0x7f) {
        output[(*used)++] = (char)codepoint;
    } else if (codepoint <= 0x7ff) {
        output[(*used)++] = (char)(0xc0 | (codepoint >> 6));
        output[(*used)++] = (char)(0x80 | (codepoint & 0x3f));
    } else if (codepoint <= 0xffff) {
        output[(*used)++] = (char)(0xe0 | (codepoint >> 12));
        output[(*used)++] = (char)(0x80 | ((codepoint >> 6) & 0x3f));
        output[(*used)++] = (char)(0x80 | (codepoint & 0x3f));
    } else {
        output[(*used)++] = (char)(0xf0 | (codepoint >> 18));
        output[(*used)++] = (char)(0x80 | ((codepoint >> 12) & 0x3f));
        output[(*used)++] = (char)(0x80 | ((codepoint >> 6) & 0x3f));
        output[(*used)++] = (char)(0x80 | (codepoint & 0x3f));
    }
}

static int parse_hex_quad(JsonParser *parser, unsigned long *value) {
    int digit;
    int i;
    unsigned long result;
    if (parser->length - parser->position < 4) return 0;
    result = 0;
    for (i = 0; i < 4; i++) {
        digit = hex_digit(parser->input[parser->position++]);
        if (digit < 0) return 0;
        result = result * 16 + (unsigned long)digit;
    }
    *value = result;
    return 1;
}

static char *parse_json_string(JsonParser *parser) {
    size_t start;
    size_t capacity;
    size_t used;
    char *output;
    char current;
    unsigned long codepoint;
    unsigned long low_surrogate;

    if (parser->position >= parser->length || parser->input[parser->position] != '"')
        return NULL;
    parser->position++;
    start = parser->position;
    capacity = parser->length - start + 1;
    output = (char *)malloc(capacity);
    if (output == NULL) {
        parser->error = "out-of-memory";
        return NULL;
    }
    used = 0;
    while (parser->position < parser->length) {
        current = parser->input[parser->position++];
        if (current == '"') {
            output[used] = '\0';
            return output;
        }
        if ((unsigned char)current < 0x20) {
            parser->error = "invalid-control-character";
            free(output);
            return NULL;
        }
        if (current != '\\') {
            output[used++] = current;
            continue;
        }
        if (parser->position >= parser->length) break;
        current = parser->input[parser->position++];
        if (current == '"' || current == '\\' || current == '/')
            output[used++] = current;
        else if (current == 'b') output[used++] = '\b';
        else if (current == 'f') output[used++] = '\f';
        else if (current == 'n') output[used++] = '\n';
        else if (current == 'r') output[used++] = '\r';
        else if (current == 't') output[used++] = '\t';
        else if (current == 'u') {
            if (!parse_hex_quad(parser, &codepoint)) {
                parser->error = "invalid-unicode-escape";
                free(output);
                return NULL;
            }
            if (codepoint >= 0xd800 && codepoint <= 0xdbff) {
                if (parser->length - parser->position < 6 ||
                    parser->input[parser->position] != '\\' ||
                    parser->input[parser->position + 1] != 'u') {
                    parser->error = "invalid-unicode-surrogate";
                    free(output);
                    return NULL;
                }
                parser->position += 2;
                if (!parse_hex_quad(parser, &low_surrogate) ||
                    low_surrogate < 0xdc00 || low_surrogate > 0xdfff) {
                    parser->error = "invalid-unicode-surrogate";
                    free(output);
                    return NULL;
                }
                codepoint = 0x10000 + ((codepoint - 0xd800) << 10) +
                    (low_surrogate - 0xdc00);
            } else if (codepoint >= 0xdc00 && codepoint <= 0xdfff) {
                parser->error = "invalid-unicode-surrogate";
                free(output);
                return NULL;
            }
            append_utf8(output, &used, codepoint);
        } else {
            parser->error = "invalid-string-escape";
            free(output);
            return NULL;
        }
    }
    parser->error = "unterminated-string";
    free(output);
    return NULL;
}

static int append_json_item(JsonValue *array, JsonValue *item) {
    JsonValue **items;
    long capacity;
    if (array->item_count == array->item_capacity) {
        capacity = array->item_capacity == 0 ? 8 : array->item_capacity * 2;
        items = (JsonValue **)realloc(array->items, (size_t)capacity * sizeof(JsonValue *));
        if (items == NULL) return 0;
        array->items = items;
        array->item_capacity = capacity;
    }
    array->items[array->item_count++] = item;
    return 1;
}

static int append_json_member(JsonValue *object, char *key, JsonValue *value) {
    JsonMember *members;
    long capacity;
    if (object->member_count == object->member_capacity) {
        capacity = object->member_capacity == 0 ? 8 : object->member_capacity * 2;
        members = (JsonMember *)realloc(object->members, (size_t)capacity * sizeof(JsonMember));
        if (members == NULL) return 0;
        object->members = members;
        object->member_capacity = capacity;
    }
    object->members[object->member_count].key = key;
    object->members[object->member_count].value = value;
    object->member_count++;
    return 1;
}

static JsonValue *parse_json_value(JsonParser *parser);

static JsonValue *parse_json_array(JsonParser *parser) {
    JsonValue *array;
    JsonValue *item;
    array = new_json_value(JSON_ARRAY);
    if (array == NULL) {
        parser->error = "out-of-memory";
        return NULL;
    }
    parser->position++;
    parser_space(parser);
    if (parser->position < parser->length && parser->input[parser->position] == ']') {
        parser->position++;
        return array;
    }
    for (;;) {
        item = parse_json_value(parser);
        if (item == NULL) return NULL;
        if (!append_json_item(array, item)) {
            parser->error = "out-of-memory";
            return NULL;
        }
        parser_space(parser);
        if (parser->position >= parser->length) break;
        if (parser->input[parser->position] == ']') {
            parser->position++;
            return array;
        }
        if (parser->input[parser->position] != ',') break;
        parser->position++;
        parser_space(parser);
    }
    parser->error = "invalid-array";
    return NULL;
}

static JsonValue *parse_json_object(JsonParser *parser) {
    JsonValue *object;
    JsonValue *value;
    char *key;
    object = new_json_value(JSON_OBJECT);
    if (object == NULL) {
        parser->error = "out-of-memory";
        return NULL;
    }
    parser->position++;
    parser_space(parser);
    if (parser->position < parser->length && parser->input[parser->position] == '}') {
        parser->position++;
        return object;
    }
    for (;;) {
        key = parse_json_string(parser);
        if (key == NULL) {
            if (parser->error == NULL) parser->error = "invalid-object-key";
            return NULL;
        }
        parser_space(parser);
        if (parser->position >= parser->length || parser->input[parser->position] != ':') {
            parser->error = "missing-object-colon";
            return NULL;
        }
        parser->position++;
        parser_space(parser);
        value = parse_json_value(parser);
        if (value == NULL) return NULL;
        if (!append_json_member(object, key, value)) {
            parser->error = "out-of-memory";
            return NULL;
        }
        parser_space(parser);
        if (parser->position >= parser->length) break;
        if (parser->input[parser->position] == '}') {
            parser->position++;
            return object;
        }
        if (parser->input[parser->position] != ',') break;
        parser->position++;
        parser_space(parser);
    }
    parser->error = "invalid-object";
    return NULL;
}

static JsonValue *parse_json_primitive(JsonParser *parser) {
    size_t start;
    size_t length;
    char *end;
    double number;
    JsonValue *value;
    start = parser->position;
    while (parser->position < parser->length &&
           parser->input[parser->position] != ',' &&
           parser->input[parser->position] != ']' &&
           parser->input[parser->position] != '}' &&
           parser->input[parser->position] != ' ' &&
           parser->input[parser->position] != '\t' &&
           parser->input[parser->position] != '\r' &&
           parser->input[parser->position] != '\n')
        parser->position++;
    length = parser->position - start;
    if (length == 0) {
        parser->error = "expected-value";
        return NULL;
    }
    value = NULL;
    if ((length == 4 && memcmp(parser->input + start, "true", 4) == 0) ||
        (length == 5 && memcmp(parser->input + start, "false", 5) == 0)) {
        value = new_json_value(JSON_BOOLEAN);
    } else if (length == 4 && memcmp(parser->input + start, "null", 4) == 0) {
        value = new_json_value(JSON_NULL);
    } else {
        errno = 0;
        number = strtod(parser->input + start, &end);
        if (errno == ERANGE || end != parser->input + parser->position ||
            number > DBL_MAX || number < -DBL_MAX) {
            parser->error = "invalid-number";
            return NULL;
        }
        value = new_json_value(JSON_NUMBER);
    }
    if (value == NULL) {
        parser->error = "out-of-memory";
        return NULL;
    }
    value->text = copy_range(parser->input + start, length);
    if (value->text == NULL) {
        parser->error = "out-of-memory";
        return NULL;
    }
    return value;
}

static JsonValue *parse_json_value(JsonParser *parser) {
    JsonValue *value;
    parser_space(parser);
    if (parser->position >= parser->length) {
        parser->error = "expected-value";
        return NULL;
    }
    if (parser->input[parser->position] == '"') {
        value = new_json_value(JSON_STRING);
        if (value == NULL) {
            parser->error = "out-of-memory";
            return NULL;
        }
        value->text = parse_json_string(parser);
        if (value->text == NULL) return NULL;
        return value;
    }
    if (parser->input[parser->position] == '[') return parse_json_array(parser);
    if (parser->input[parser->position] == '{') return parse_json_object(parser);
    return parse_json_primitive(parser);
}

static JsonValue *parse_json_document(const char *input, size_t length, const char **error) {
    JsonParser parser;
    JsonValue *root;
    parser.input = input;
    parser.length = length;
    parser.position = 0;
    parser.error = NULL;
    root = parse_json_value(&parser);
    parser_space(&parser);
    if (root == NULL || parser.position != parser.length) {
        if (parser.error == NULL) parser.error = "trailing-json-data";
        *error = parser.error;
        return NULL;
    }
    *error = NULL;
    return root;
}

static JsonValue *json_get(const JsonValue *object, const char *name) {
    long i;
    if (object == NULL || object->type != JSON_OBJECT) return NULL;
    for (i = 0; i < object->member_count; i++)
        if (strcmp(object->members[i].key, name) == 0)
            return object->members[i].value;
    return NULL;
}

static JsonValue *json_get_any(const JsonValue *object, const char *first,
        const char *second, const char *third) {
    JsonValue *value;
    value = json_get(object, first);
    if (value == NULL && second != NULL) value = json_get(object, second);
    if (value == NULL && third != NULL) value = json_get(object, third);
    return value;
}

static JsonValue *json_at(const JsonValue *array, long index) {
    if (array == NULL || array->type != JSON_ARRAY || index < 0 || index >= array->item_count)
        return NULL;
    return array->items[index];
}

static const char *json_text(const JsonValue *value) {
    if (value == NULL || value->type == JSON_NULL || value->type == JSON_OBJECT ||
        value->type == JSON_ARRAY)
        return NULL;
    return value->text;
}

static const char *json_identity(const JsonValue *value) {
    if (value != NULL && value->type == JSON_OBJECT)
        value = json_get_any(value, "id", "playerId", "playerID");
    return json_text(value);
}

static int json_double_value(const JsonValue *value, double *number) {
    const char *text;
    char *end;
    double parsed;
    text = json_text(value);
    if (text == NULL || value->type == JSON_BOOLEAN) return 0;
    errno = 0;
    parsed = strtod(text, &end);
    if (errno == ERANGE || *text == '\0' || *end != '\0' ||
        parsed > DBL_MAX || parsed < -DBL_MAX)
        return 0;
    *number = parsed;
    return 1;
}

static int json_long_value(const JsonValue *value, long *number) {
    double parsed;
    if (!json_double_value(value, &parsed) || parsed < (double)LONG_MIN ||
        parsed > (double)LONG_MAX || floor(parsed) != parsed)
        return 0;
    *number = (long)parsed;
    return 1;
}

static int json_boolean_value(const JsonValue *value, int default_value) {
    if (value == NULL) return default_value;
    if (value->type != JSON_BOOLEAN) return default_value;
    return strcmp(value->text, "true") == 0;
}

static void write_json_string(const char *value) {
    const unsigned char *cursor;
    printf("\"");
    if (value == NULL) {
        printf("\"");
        return;
    }
    for (cursor = (const unsigned char *)value; *cursor; cursor++) {
        if (*cursor == '"') printf("\\\"");
        else if (*cursor == '\\') printf("\\\\");
        else if (*cursor == '\b') printf("\\b");
        else if (*cursor == '\f') printf("\\f");
        else if (*cursor == '\n') printf("\\n");
        else if (*cursor == '\r') printf("\\r");
        else if (*cursor == '\t') printf("\\t");
        else if (*cursor < 0x20) printf("\\u%04x", (unsigned int)*cursor);
        else putchar((int)*cursor);
    }
    printf("\"");
}

static void write_nullable_string(const char *value) {
    if (value == NULL) printf("null");
    else write_json_string(value);
}

static void write_json_value(const JsonValue *value) {
    long i;
    if (value == NULL || value->type == JSON_NULL) {
        printf("null");
    } else if (value->type == JSON_STRING) {
        write_json_string(value->text);
    } else if (value->type == JSON_NUMBER || value->type == JSON_BOOLEAN) {
        printf("%s", value->text);
    } else if (value->type == JSON_ARRAY) {
        putchar('[');
        for (i = 0; i < value->item_count; i++) {
            if (i) putchar(',');
            write_json_value(value->items[i]);
        }
        putchar(']');
    } else {
        putchar('{');
        for (i = 0; i < value->member_count; i++) {
            if (i) putchar(',');
            write_json_string(value->members[i].key);
            putchar(':');
            write_json_value(value->members[i].value);
        }
        putchar('}');
    }
}

static int equal_case_prefix(const char *left, const char *right, size_t length) {
    size_t i;
    if (left == NULL || right == NULL) return 0;
    for (i = 0; i < length; i++) {
        if (left[i] == '\0' || right[i] == '\0' ||
            tolower((unsigned char)left[i]) != tolower((unsigned char)right[i]))
            return 0;
    }
    return 1;
}

static void set_tournament_error(const char *code, const char *message) {
    if (code == NULL) code = "papp-c-error";
    if (message == NULL) message = "PAPP C tournament operation failed";
    sprintf(tournament_error, "%s|%s", code, message);
}

static void write_error_response(void) {
    const char *separator;
    char code[96];
    size_t length;
    separator = strchr(tournament_error, '|');
    printf("{\"ok\":false,\"source\":\"papp-c\",\"code\":");
    if (separator == NULL) write_json_string("papp-c-error");
    else {
        length = (size_t)(separator - tournament_error);
        if (length >= sizeof(code)) length = sizeof(code) - 1;
        memcpy(code, tournament_error, length);
        code[length] = '\0';
        write_json_string(code);
    }
    printf(",\"message\":");
    if (separator == NULL) write_json_string("PAPP C tournament operation failed");
    else write_json_string(separator + 1);
    printf("}\n");
}

static int score_value(const JsonValue *value, long *score_value_out) {
    long parsed;
    if (!json_long_value(value, &parsed) || parsed < 0 || parsed > 64) return 0;
    *score_value_out = parsed;
    return 1;
}

static int operation_round_count(const JsonValue *root) {
    double player_count_value;
    long player_count;
    long rounds;
    JsonValue *player_count_json;
    JsonValue *manual_json;
    long manual_rounds;

    player_count_json = json_get_any(root, "playerCount", "participantCount", NULL);
    if (!json_double_value(player_count_json, &player_count_value) || player_count_value == 0.0)
        player_count = 0;
    else {
        if (player_count_value > (double)LONG_MAX || player_count_value < (double)LONG_MIN) {
            set_tournament_error("invalid-player-count", "选手人数超出可计算范围");
            return 0;
        }
        player_count = (long)player_count_value;
    }
    if (player_count < 1) player_count = 1;

    manual_json = json_get(root, "manualRoundCount");
    if (manual_json != NULL && manual_json->type != JSON_NULL) {
        if (!json_long_value(manual_json, &manual_rounds) || manual_rounds < 1 ||
            manual_rounds > NMAX_ROUNDS) {
            set_tournament_error("invalid-round-count", "手动预赛轮数必须是 1 到 128 的整数");
            return 0;
        }
        rounds = manual_rounds;
    } else {
        rounds = (long)ceil(log((double)player_count) / log(2.0));
        if (rounds < 4) rounds = 4;
        if (rounds > NMAX_ROUNDS) {
            set_tournament_error("invalid-round-count", "自动预赛轮数超过 PAPP C 的 128 轮上限");
            return 0;
        }
    }
    printf("{\"ok\":true,\"source\":\"papp-c\",\"operation\":\"round-count\",\"playerCount\":%ld,\"roundCount\":%ld}\n",
        player_count, rounds);
    return 1;
}

static int operation_validate_score(const JsonValue *root) {
    JsonValue *black_json;
    JsonValue *white_json;
    long black_score;
    long white_score;
    int has_black;
    int has_white;
    black_json = json_get(root, "blackScore");
    white_json = json_get(root, "whiteScore");
    has_black = black_json != NULL && black_json->type != JSON_NULL &&
        json_text(black_json) != NULL && *json_text(black_json) != '\0';
    has_white = white_json != NULL && white_json->type != JSON_NULL &&
        json_text(white_json) != NULL && *json_text(white_json) != '\0';
    if (!has_black && !has_white) {
        printf("{\"ok\":true,\"source\":\"papp-c\",\"operation\":\"validate-score\",\"complete\":false,\"scorePair\":null}\n");
        return 1;
    }
    if (has_black && !score_value(black_json, &black_score)) {
        set_tournament_error("invalid-score", "黑方棋子数必须是 0 到 64 的整数");
        return 0;
    }
    if (has_white && !score_value(white_json, &white_score)) {
        set_tournament_error("invalid-score", "白方棋子数必须是 0 到 64 的整数");
        return 0;
    }
    if (!has_black) black_score = 64 - white_score;
    if (!has_white) white_score = 64 - black_score;
    if (black_score + white_score != 64) {
        set_tournament_error("invalid-score-pair", "黑白双方棋子数之和必须为 64");
        return 0;
    }
    printf("{\"ok\":true,\"source\":\"papp-c\",\"operation\":\"validate-score\",\"complete\":true,\"scorePair\":{\"blackScore\":%ld,\"whiteScore\":%ld}}\n",
        black_score, white_score);
    return 1;
}

static int find_player_index(const char *id) {
    long i;
    if (id == NULL) return -1;
    for (i = 0; i < tournament_player_count; i++)
        if (strcmp(tournament_players[i].id, id) == 0)
            return (int)i;
    return -1;
}

static int add_player_from_json(const JsonValue *player_json, long index) {
    JsonValue *id_json;
    JsonValue *name_json;
    JsonValue *account_json;
    const char *id;
    const char *name;
    const char *account;
    Player *player;
    long i;

    id_json = json_get_any(player_json, "id", "playerId", "candidateId");
    name_json = json_get_any(player_json, "displayName", "pappName", "name");
    account_json = json_get_any(player_json, "account", "oqAccount", NULL);
    id = json_identity(id_json);
    name = json_text(name_json);
    account = json_text(account_json);
    if (id == NULL || *id == '\0' || name == NULL || *name == '\0') {
        set_tournament_error("invalid-player", "每位选手必须提供非空 id 和 displayName");
        return 0;
    }
    for (i = 0; i < index; i++) {
        if (strcmp(tournament_players[i].id, id) == 0) {
            set_tournament_error("duplicate-player-id", "选手 id 必须唯一");
            return 0;
        }
    }
    tournament_players[index].id = id;
    tournament_players[index].name = name;
    tournament_players[index].account = account;
    tournament_players[index].papp_id = index + 1;
    player = new_player(index + 1, name, NULL, NULL, "online", 0, NULL, 0);
    if (player == NULL) {
        set_tournament_error("player-create-failed", "PAPP C 无法初始化选手");
        return 0;
    }
    addPlayer(registered_players, player);
    return 1;
}

static int configure_core(const JsonValue *root) {
    JsonValue *players_json;
    JsonValue *parameters_json;
    JsonValue *constant_json;
    double constant;
    long rounds;
    long i;
    Player *player;

    players_json = json_get(root, "players");
    if (players_json == NULL || players_json->type != JSON_ARRAY ||
        players_json->item_count < 1 || players_json->item_count > TOURNAMENT_MAX_PLAYERS) {
        set_tournament_error("invalid-player-count", "PAPP C 每次调用需要 1 到 256 名选手");
        return 0;
    }
    parameters_json = json_get(root, "tournamentParameters");
    constant_json = json_get(root, "brightwellConstant");
    if (constant_json == NULL) constant_json = json_get(parameters_json, "brightwellConstant");
    if (!json_double_value(constant_json, &constant)) constant = 6.0;
    if (constant < 0.0 || constant > DBL_MAX) {
        set_tournament_error("invalid-brightwell", "Brightwell 常数必须是非负数");
        return 0;
    }
    if (!json_long_value(json_get_any(root, "preliminaryRoundCount", "roundCount", NULL), &rounds) ||
        rounds < 1 || rounds > NMAX_ROUNDS) {
        set_tournament_error("invalid-round-count", "预赛轮数必须是 1 到 128 的整数");
        return 0;
    }

    registered_players = createList();
    new_players = createList();
    emigrant_players = createList();
    team_captain = createList();
    first_round();
    init_default_penalties();
    papp_tournament_api = 1;
    number_of_rounds = rounds;
    brightwell_coeff = constant / 2.0;
    pairings_file_save = 0;
    generate_xml_files = 0;
    tournament_player_count = players_json->item_count;
    for (i = 0; i < tournament_player_count; i++) {
        if (!add_player_from_json(json_at(players_json, i), i)) return 0;
    }
    do_not_save_registered();
    do_not_save_pairings();

    for (i = 0; i < registered_players->n; i++) {
        player = registered_players->list[i];
        if (player == NULL || player->fullname == NULL) {
            set_tournament_error("player-create-failed", "PAPP C 初始化选手姓名失败");
            return 0;
        }
    }
    return 1;
}

static int set_presence_from_ids(const JsonValue *ids, int default_all) {
    long i;
    long index;
    const char *id;
    JsonValue *item;
    for (i = 0; i < tournament_player_count; i++) present[i] = 0;
    if (ids == NULL || ids->type == JSON_NULL) {
        if (default_all)
            for (i = 0; i < tournament_player_count; i++) present[i] = 1;
        return 1;
    }
    if (ids->type != JSON_ARRAY) {
        set_tournament_error("invalid-presence", "presentPlayerIds 必须是选手 id 列表");
        return 0;
    }
    for (i = 0; i < ids->item_count; i++) {
        item = json_at(ids, i);
        id = json_identity(item);
        index = find_player_index(id);
        if (index < 0) {
            set_tournament_error("unknown-player", "签到列表引用了未知选手");
            return 0;
        }
        present[index] = 1;
    }
    return 1;
}

static int player_id_from_pairing(const JsonValue *pairing, int bye, int black_side) {
    JsonValue *value;
    const char *id;
    int index;
    if (bye) {
        value = json_get_any(pairing, "playerId", "byePlayerId", "blackId");
        if (value == NULL) value = json_get(pairing, "black");
    } else if (black_side) {
        value = json_get_any(pairing, "blackId", "blackPlayerId", "black");
    } else {
        value = json_get_any(pairing, "whiteId", "whitePlayerId", "white");
    }
    id = json_identity(value);
    index = find_player_index(id);
    return index;
}

static int read_pairing_score(const JsonValue *pairing, long *black_score, long *white_score) {
    JsonValue *black_json;
    JsonValue *white_json;
    black_json = json_get(pairing, "blackScore");
    white_json = json_get(pairing, "whiteScore");
    if (!score_value(black_json, black_score) || !score_value(white_json, white_score) ||
        *black_score + *white_score != 64)
        return 0;
    return 1;
}

static int read_completed_pairing_score(const JsonValue *pairing,
        long *black_score, long *white_score) {
    const char *status;
    status = json_text(json_get(pairing, "status"));
    return status != NULL && strcmp(status, "completed") == 0 &&
        read_pairing_score(pairing, black_score, white_score);
}

static int round_is_bye(const JsonValue *pairing) {
    const char *status;
    status = json_text(json_get(pairing, "status"));
    return status != NULL && strcmp(status, "bye") == 0;
}

static JsonValue *find_round(const JsonValue *rounds_json, long round_number) {
    JsonValue *item;
    JsonValue *round_json;
    long i;
    long value;
    if (rounds_json == NULL || rounds_json->type != JSON_ARRAY) return NULL;
    for (i = 0; i < rounds_json->item_count; i++) {
        item = json_at(rounds_json, i);
        round_json = json_get(item, "round");
        if (json_long_value(round_json, &value)) {
            if (value == round_number) return item;
        } else if (i + 1 == round_number) return item;
    }
    return NULL;
}

typedef struct {
    long expected_rounds;
    long rounds_with_pairings;
    long unresolved_pairings;
    long missing_count;
    long missing[ NMAX_ROUNDS ];
    int complete;
} RoundProgress;

static RoundProgress calculate_progress(const JsonValue *root) {
    RoundProgress progress;
    JsonValue *rounds_json;
    JsonValue *round_json;
    JsonValue *pairings_json;
    JsonValue *pairing_json;
    long round_number;
    long i;
    long black_score;
    long white_score;

    memset(&progress, 0, sizeof(progress));
    json_long_value(json_get_any(root, "preliminaryRoundCount", "roundCount", NULL),
        &progress.expected_rounds);
    if (progress.expected_rounds < 1 || progress.expected_rounds > NMAX_ROUNDS)
        progress.expected_rounds = 1;
    rounds_json = json_get(root, "rounds");
    for (round_number = 1; round_number <= progress.expected_rounds; round_number++) {
        round_json = find_round(rounds_json, round_number);
        pairings_json = json_get(round_json, "pairings");
        if (pairings_json == NULL || pairings_json->type != JSON_ARRAY ||
            pairings_json->item_count == 0) {
            progress.missing[progress.missing_count++] = round_number;
            continue;
        }
        progress.rounds_with_pairings++;
        for (i = 0; i < pairings_json->item_count; i++) {
            pairing_json = json_at(pairings_json, i);
            if (!round_is_bye(pairing_json) &&
                !read_completed_pairing_score(pairing_json, &black_score, &white_score))
                progress.unresolved_pairings++;
        }
    }
    progress.complete = progress.missing_count == 0 && progress.unresolved_pairings == 0;
    return progress;
}

static void write_progress(const RoundProgress *progress) {
    long i;
    printf("{\"expectedRounds\":%ld,\"roundsWithPairings\":%ld,\"missingRounds\":[",
        progress->expected_rounds, progress->rounds_with_pairings);
    for (i = 0; i < progress->missing_count; i++) {
        if (i) putchar(',');
        printf("%ld", progress->missing[i]);
    }
    printf("],\"unresolvedPairings\":%ld,\"complete\":%s}",
        progress->unresolved_pairings, progress->complete ? "true" : "false");
}

static int load_one_history_round(const JsonValue *round_json, long expected_round) {
    JsonValue *pairings_json;
    JsonValue *pairing_json;
    JsonValue *present_json;
    long i;
    long black_score;
    long white_score;
    long black_index;
    long white_index;
    long bye_index;
    long j;
    long n1;
    long n2;
    int seen[TOURNAMENT_MAX_PLAYERS];
    pairings_json = json_get(round_json, "pairings");
    if (pairings_json == NULL || pairings_json->type != JSON_ARRAY || pairings_json->item_count == 0) {
        set_tournament_error("missing-round-pairings", "預賽歷史包含缺少配對的輪次");
        return 0;
    }
    memset(seen, 0, sizeof(seen));
    present_json = json_get(round_json, "presentPlayerIds");
    if (present_json != NULL) {
        if (!set_presence_from_ids(present_json, 0)) return 0;
    } else {
        for (i = 0; i < tournament_player_count; i++) present[i] = 0;
        for (i = 0; i < pairings_json->item_count; i++) {
            pairing_json = json_at(pairings_json, i);
            if (round_is_bye(pairing_json)) {
                bye_index = player_id_from_pairing(pairing_json, 1, 1);
                if (bye_index < 0) {
                    set_tournament_error("invalid-bye", "輪空記錄引用了未知選手");
                    return 0;
                }
                present[bye_index] = 1;
            } else {
                black_index = player_id_from_pairing(pairing_json, 0, 1);
                white_index = player_id_from_pairing(pairing_json, 0, 0);
                if (black_index < 0 || white_index < 0 || black_index == white_index) {
                    set_tournament_error("invalid-pairing", "預賽歷史配對的選手 id 无效");
                    return 0;
                }
                present[black_index] = 1;
                present[white_index] = 1;
            }
        }
    }
    current_round = expected_round - 1;
    for (i = 0; i < pairings_json->item_count; i++) {
        pairing_json = json_at(pairings_json, i);
        if (round_is_bye(pairing_json)) {
            bye_index = player_id_from_pairing(pairing_json, 1, 1);
            if (bye_index < 0 || seen[bye_index]) {
                set_tournament_error("invalid-bye", "輪空记录重复或引用未知选手");
                return 0;
            }
            seen[bye_index] = 1;
            continue;
        }
        black_index = player_id_from_pairing(pairing_json, 0, 1);
        white_index = player_id_from_pairing(pairing_json, 0, 0);
        if (black_index < 0 || white_index < 0 || black_index == white_index ||
            seen[black_index] || seen[white_index]) {
            set_tournament_error("invalid-pairing", "预赛轮次中存在重复选手或无效配对");
            return 0;
        }
        seen[black_index] = 1;
        seen[white_index] = 1;
        if (!read_completed_pairing_score(pairing_json, &black_score, &white_score)) {
            set_tournament_error("unresolved-pairing", "预赛仍有未确认或无效比分");
            return 0;
        }
        n1 = tournament_players[black_index].papp_id;
        n2 = tournament_players[white_index].papp_id;
        make_couple(n1, n2, INTEGER_TO_SCORE(black_score));
    }
    for (j = 0; j < tournament_player_count; j++) {
        if (!present[j] && seen[j]) {
            set_tournament_error("invalid-presence", "輪次配對与签到名单不一致");
            return 0;
        }
    }
    updateScores();
    if (expected_round < NMAX_ROUNDS)
        next_round();
    else {
        for (i = 0; i < tournament_player_count; i++)
            set_history_presence(tournament_players[i].papp_id, expected_round - 1, present[i]);
        current_round = NMAX_ROUNDS;
    }
    return 1;
}

static int load_preliminary_history(const JsonValue *root, long history_count,
        int need_tiebreak) {
    JsonValue *rounds_json;
    JsonValue *round_json;
    long i;
    rounds_json = json_get(root, "rounds");
    if (history_count > NMAX_ROUNDS || history_count < 0) {
        set_tournament_error("invalid-history", "预赛轮数超出 PAPP C 支持范围");
        return 0;
    }
    for (i = 1; i <= history_count; i++) {
        round_json = find_round(rounds_json, i);
        if (round_json == NULL) {
            set_tournament_error("missing-round", "预赛历史轮次不连续");
            return 0;
        }
        if (!load_one_history_round(round_json, i)) return 0;
    }
    if (history_count == 0) current_round = 0;
    if (need_tiebreak && history_count > 0) tieBreak_computation();
    return 1;
}

static void calculate_ranking(long *order, long *rank_by_player) {
    long i;
    tieBreak_computation();
    for (i = 0; i < tournament_player_count; i++) order[i] = i;
    SORT(order, tournament_player_count, sizeof(long), sort_players);
    for (i = 0; i < tournament_player_count; i++) rank_by_player[order[i]] = i + 1;
}

static void write_player_metric(long player_index, long rank, long preliminary_rank) {
    double display_points;
    long points_half_units;
    double total_discs;
    points_half_units = score[player_index];
    display_points = (double)points_half_units / 2.0;
    total_discs = (double)SCORE_TO_FLOAT(nbr_discs[player_index]);
    printf("{\"rank\":%ld,\"preliminaryRank\":%ld,\"playerId\":", rank, preliminary_rank);
    write_json_string(tournament_players[player_index].id);
    printf(",\"displayName\":");
    write_json_string(tournament_players[player_index].name);
    printf(",\"account\":");
    write_nullable_string(tournament_players[player_index].account);
    printf(",\"pointsHalfUnits\":%ld,\"displayPoints\":%.15g,\"totalPoints\":%.15g,\"brightwell\":%.15g,\"totalDiscs\":%.15g}",
        points_half_units, display_points, display_points, tieBreak[player_index], total_discs);
}

static void write_standings(const long *order, const long *rank_by_player,
        const long *overall_rank, int has_playoffs) {
    long i;
    long player_index;
    for (i = 0; i < tournament_player_count; i++) {
        player_index = order[i];
        if (i) printf(",\n");
        write_player_metric(player_index,
            overall_rank == NULL ? i + 1 : overall_rank[player_index],
            rank_by_player[player_index]);
    }
    (void)has_playoffs;
}

static void write_pairing_prefix(const char *operation, const char *stage,
        long round_number, long table) {
    printf("{\"id\":");
    {
        char id[128];
        sprintf(id, "%s-%s-r%ld-t%ld", operation, stage, round_number, table);
        write_json_string(id);
    }
    printf(",\"stage\":");
    write_json_string(stage);
    printf(",\"round\":%ld,\"table\":%ld,\"source\":\"papp-c\"", round_number, table);
}

static void write_player_side(const char *side, long player_index) {
    printf(",\"%sId\":", side);
    write_json_string(tournament_players[player_index].id);
    printf(",\"%sName\":", side);
    write_json_string(tournament_players[player_index].name);
    printf(",\"%sAccount\":", side);
    write_nullable_string(tournament_players[player_index].account);
}

static void write_current_pairings(const char *stage, long round_number,
        long *rank_by_player, const char *operation) {
    long n1;
    long n2;
    long table;
    long i1;
    long i2;
    long bye_table;
    discs_t value;
    int first;
    round_iterate(current_round);
    table = 0;
    first = 1;
    printf("[\n");
    while (next_couple(&n1, &n2, &value)) {
        i1 = inscription_ID(n1);
        i2 = inscription_ID(n2);
        if (i1 < 0 || i2 < 0) continue;
        table++;
        if (!first) printf(",\n");
        first = 0;
        write_pairing_prefix(operation, stage, round_number, table);
        write_player_side("black", i1);
        write_player_side("white", i2);
        printf(",\"blackScore\":null,\"whiteScore\":null,\"status\":\"imported\"}");
    }
    bye_table = table;
    for (i1 = 0; i1 < tournament_player_count; i1++) {
        if (present[i1] && polarity(tournament_players[i1].papp_id) == 0) {
            bye_table++;
            if (!first) printf(",\n");
            first = 0;
            write_pairing_prefix(operation, stage, round_number, bye_table);
            printf(",\"playerId\":");
            write_json_string(tournament_players[i1].id);
            printf(",\"playerName\":");
            write_json_string(tournament_players[i1].name);
            printf(",\"account\":");
            write_nullable_string(tournament_players[i1].account);
            printf(",\"blackId\":");
            write_json_string(tournament_players[i1].id);
            printf(",\"black\":");
            write_json_string(tournament_players[i1].name);
            printf(",\"blackName\":");
            write_json_string(tournament_players[i1].name);
            printf(",\"white\":\"BYE\",\"whiteName\":\"BYE\",\"blackScore\":40,\"whiteScore\":24,\"status\":\"bye\",\"pointsHalfUnits\":2,\"displayPoints\":1,\"discs\":40}");
        }
    }
    printf("\n]");
    (void)rank_by_player;
}

static int history_round_complete(const JsonValue *round_json) {
    JsonValue *pairings_json;
    JsonValue *pairing_json;
    long i;
    long black_score;
    long white_score;
    pairings_json = json_get(round_json, "pairings");
    if (pairings_json == NULL || pairings_json->type != JSON_ARRAY ||
        pairings_json->item_count == 0)
        return 0;
    for (i = 0; i < pairings_json->item_count; i++) {
        pairing_json = json_at(pairings_json, i);
        if (!round_is_bye(pairing_json) &&
            !read_completed_pairing_score(pairing_json, &black_score, &white_score))
            return 0;
    }
    return 1;
}

enum HistoryRoundState {
    HISTORY_ROUND_INVALID = -1,
    HISTORY_ROUND_MISSING = 0,
    HISTORY_ROUND_INCOMPLETE = 1,
    HISTORY_ROUND_COMPLETE = 2
};

static int score_field_present(const JsonValue *value) {
    const char *text;
    if (value == NULL || value->type == JSON_NULL) return 0;
    text = json_text(value);
    return text == NULL || *text != '\0';
}

static int history_round_state(const JsonValue *round_json) {
    JsonValue *pairings_json;
    JsonValue *pairing_json;
    JsonValue *black_json;
    JsonValue *white_json;
    const char *status;
    long black_score;
    long white_score;
    long i;
    long unresolved_pairings;
    int has_black_score;
    int has_white_score;

    unresolved_pairings = 0;
    if (round_json == NULL) return HISTORY_ROUND_MISSING;
    pairings_json = json_get(round_json, "pairings");
    if (pairings_json == NULL || pairings_json->type != JSON_ARRAY ||
        pairings_json->item_count == 0)
        return HISTORY_ROUND_MISSING;
    for (i = 0; i < pairings_json->item_count; i++) {
        pairing_json = json_at(pairings_json, i);
        if (round_is_bye(pairing_json)) continue;
        black_json = json_get(pairing_json, "blackScore");
        white_json = json_get(pairing_json, "whiteScore");
        has_black_score = score_field_present(black_json);
        has_white_score = score_field_present(white_json);
        if (has_black_score && !score_value(black_json, &black_score)) {
            set_tournament_error("invalid-score", "黑方棋子数必须是 0 到 64 的整数");
            return HISTORY_ROUND_INVALID;
        }
        if (has_white_score && !score_value(white_json, &white_score)) {
            set_tournament_error("invalid-score", "白方棋子数必须是 0 到 64 的整数");
            return HISTORY_ROUND_INVALID;
        }
        if (has_black_score && has_white_score && black_score + white_score != 64) {
            set_tournament_error("invalid-score-pair", "黑白双方棋子数之和必须为 64");
            return HISTORY_ROUND_INVALID;
        }
        status = json_text(json_get(pairing_json, "status"));
        if (status == NULL || strcmp(status, "completed") != 0 ||
            !has_black_score || !has_white_score)
            unresolved_pairings++;
    }
    return unresolved_pairings == 0
        ? HISTORY_ROUND_COMPLETE : HISTORY_ROUND_INCOMPLETE;
}

static const char *history_round_state_name(int state) {
    if (state == HISTORY_ROUND_COMPLETE) return "complete";
    if (state == HISTORY_ROUND_MISSING) return "missing";
    return "incomplete";
}

static const char *history_round_state_code(int state) {
    if (state == HISTORY_ROUND_MISSING) return "round-missing";
    if (state == HISTORY_ROUND_INCOMPLETE) return "round-results-incomplete";
    return "round-results-invalid";
}

static const char *preliminary_next_stage(const RoundProgress *progress,
        int has_playoffs) {
    if (!progress->complete) return "preliminary-registration";
    return has_playoffs ? "preliminary-ranking" : "overall-ranking";
}

static void write_round_standings_prefix(long requested_round,
        const RoundProgress *progress, int has_playoffs) {
    printf("{\"ok\":true,\"source\":\"papp-c\",\"operation\":\"round-standings\",\"stage\":\"preliminary\",\"round\":%ld,\"targetRound\":%ld,\"throughRound\":%ld,\"preliminaryRoundCount\":%ld,\"participantCount\":%ld,\"hasSemifinalAndFinal\":%s,\"pointsUnit\":\"half-point-ticks\",\"progress\":",
        requested_round, requested_round, requested_round,
        progress->expected_rounds,
        tournament_player_count, has_playoffs ? "true" : "false");
    write_progress(progress);
}

static int playoffs_enabled(const JsonValue *root);

static int operation_round_standings(const JsonValue *root) {
    RoundProgress progress;
    JsonValue *rounds_json;
    JsonValue *round_json;
    long order[TOURNAMENT_MAX_PLAYERS];
    long ranks[TOURNAMENT_MAX_PLAYERS];
    long requested_round;
    long blocking_round;
    long i;
    int target_state;
    int state;
    int blocking_state;
    int has_playoffs;
    const char *message;

    if (!configure_core(root)) return 0;
    if (!json_long_value(json_get(root, "round"), &requested_round) ||
        requested_round < 1 || requested_round > number_of_rounds) {
        set_tournament_error("invalid-round-index", "目标轮次必须在 1 到预赛轮数范围内");
        return 0;
    }

    progress = calculate_progress(root);
    has_playoffs = playoffs_enabled(root);
    rounds_json = json_get(root, "rounds");
    target_state = HISTORY_ROUND_MISSING;
    blocking_round = 0;
    blocking_state = HISTORY_ROUND_COMPLETE;
    for (i = 1; i <= requested_round; i++) {
        round_json = find_round(rounds_json, i);
        state = history_round_state(round_json);
        if (state == HISTORY_ROUND_INVALID) return 0;
        if (i == requested_round) target_state = state;
        if (state != HISTORY_ROUND_COMPLETE && blocking_round == 0) {
            blocking_round = i;
            blocking_state = state;
        }
    }

    if (blocking_round != 0) {
        write_round_standings_prefix(requested_round, &progress, has_playoffs);
        message = blocking_state == HISTORY_ROUND_MISSING
            ? "目标轮次或其之前的预赛轮次尚未登记配对"
            : "目标轮次或其之前的预赛轮次仍有未由 PAPP C 确认的比分";
        printf(",\"complete\":false,\"status\":\"incomplete\",\"roundComplete\":%s,\"roundStatus\":",
            target_state == HISTORY_ROUND_COMPLETE ? "true" : "false");
        write_json_string(history_round_state_name(target_state));
        printf(",\"blockingRound\":%ld,\"code\":", blocking_round);
        write_json_string(history_round_state_code(blocking_state));
        printf(",\"message\":");
        write_json_string(message);
        printf(",\"standings\":[],\"stageProgress\":{\"complete\":%s,\"cumulativeHistoryComplete\":false},\"nextStage\":",
            progress.complete ? "true" : "false");
        write_json_string(preliminary_next_stage(&progress, has_playoffs));
        printf("}\n");
        return 1;
    }

    if (!load_preliminary_history(root, requested_round, 1)) return 0;
    calculate_ranking(order, ranks);
    write_round_standings_prefix(requested_round, &progress, has_playoffs);
    printf(",\"complete\":true,\"status\":\"complete\",\"roundComplete\":true,\"roundStatus\":\"complete\",\"standings\":[");
    write_standings(order, ranks, NULL, has_playoffs);
    printf("],\"stageProgress\":{\"complete\":%s,\"cumulativeHistoryComplete\":true},\"nextStage\":",
        progress.complete ? "true" : "false");
    write_json_string(preliminary_next_stage(&progress, has_playoffs));
    printf("}\n");
    return 1;
}

static int playoffs_enabled(const JsonValue *root) {
    JsonValue *parameters_json;
    parameters_json = json_get(root, "tournamentParameters");
    return json_boolean_value(
        json_get_any(root, "hasSemifinalAndFinal", NULL, NULL),
        json_boolean_value(json_get(parameters_json, "hasSemifinalAndFinal"), 1));
}

static int pairing_contains_players(const JsonValue *pairing, long first, long second) {
    long black_index;
    long white_index;
    black_index = player_id_from_pairing(pairing, 0, 1);
    white_index = player_id_from_pairing(pairing, 0, 0);
    return (black_index == first && white_index == second) ||
        (black_index == second && white_index == first);
}

static int find_pairing_for_players(const JsonValue *pairings, long first, long second,
        JsonValue **found) {
    long i;
    long match_count;
    JsonValue *pairing;
    match_count = 0;
    *found = NULL;
    if (pairings == NULL || pairings->type != JSON_ARRAY) return 0;
    for (i = 0; i < pairings->item_count; i++) {
        pairing = json_at(pairings, i);
        if (pairing_contains_players(pairing, first, second)) {
            *found = pairing;
            match_count++;
        }
    }
    return match_count == 1;
}

static int playoff_outcome(const JsonValue *pairing, long seed_a, long seed_b,
        const long *rank_by_player, long *winner, long *loser) {
    long black_index;
    long white_index;
    long black_score;
    long white_score;
    const char *status;
    black_index = player_id_from_pairing(pairing, 0, 1);
    white_index = player_id_from_pairing(pairing, 0, 0);
    if (!((black_index == seed_a && white_index == seed_b) ||
          (black_index == seed_b && white_index == seed_a))) return 0;
    status = json_text(json_get(pairing, "status"));
    if (status == NULL || strcmp(status, "completed") != 0 ||
        !read_pairing_score(pairing, &black_score, &white_score)) return 0;
    if (black_score > white_score) *winner = black_index;
    else if (white_score > black_score) *winner = white_index;
    else *winner = rank_by_player[black_index] < rank_by_player[white_index]
        ? black_index : white_index;
    *loser = *winner == black_index ? white_index : black_index;
    return 1;
}

static int validate_playoff_pairing_set(const JsonValue *root, JsonValue *pairings,
        const char *stage) {
    RoundProgress progress;
    JsonValue *semifinals;
    JsonValue *match;
    JsonValue *first_match;
    JsonValue *second_match;
    JsonValue *third_match;
    JsonValue *phase_json;
    const char *phase;
    long order[TOURNAMENT_MAX_PLAYERS];
    long ranks[TOURNAMENT_MAX_PLAYERS];
    long semifinal_seeds[2][2];
    long semifinal_winners[2];
    long semifinal_losers[2];
    long i;

    if (pairings == NULL || pairings->type != JSON_ARRAY || pairings->item_count != 2) {
        set_tournament_error("playoff-pairings-invalid", "淘汰赛必须恰好包含两场配对");
        return 0;
    }
    if (!playoffs_enabled(root)) {
        set_tournament_error("playoffs-disabled", "赛事未启用半决赛和决赛");
        return 0;
    }
    progress = calculate_progress(root);
    if (!progress.complete) {
        set_tournament_error("preliminary-results-incomplete", "预赛配对或比分尚未全部由 PAPP C 确认");
        return 0;
    }
    if (tournament_player_count < 4) {
        set_tournament_error("playoff-players-missing", "淘汰赛至少需要 4 名已签到选手");
        return 0;
    }
    if (!load_preliminary_history(root, progress.expected_rounds, 1)) return 0;
    calculate_ranking(order, ranks);
    semifinal_seeds[0][0] = order[0];
    semifinal_seeds[0][1] = order[3];
    semifinal_seeds[1][0] = order[1];
    semifinal_seeds[1][1] = order[2];

    if (strcmp(stage, "semifinal") == 0) {
        for (i = 0; i < 2; i++) {
            match = NULL;
            if (!find_pairing_for_players(pairings, semifinal_seeds[i][0],
                    semifinal_seeds[i][1], &match)) {
                set_tournament_error("semifinal-pairings-invalid", "半决赛配对与预赛 C 前四种子不一致");
                return 0;
            }
            phase_json = json_get(match, "phase");
            phase = json_text(phase_json);
            if (phase != NULL && strcmp(phase, "semifinal") != 0) {
                set_tournament_error("semifinal-pairings-invalid", "半决赛配对阶段标记无效");
                return 0;
            }
        }
        return 1;
    }

    if (strcmp(stage, "placement") != 0) {
        set_tournament_error("unsupported-stage", "PAPP C 不支持此淘汰赛阶段");
        return 0;
    }
    semifinals = json_get(root, "semifinalPairings");
    if (semifinals == NULL && json_get(root, "playoffRegistration") != NULL)
        semifinals = json_get(json_get(root, "playoffRegistration"), "semifinalPairings");
    first_match = NULL;
    second_match = NULL;
    if (semifinals == NULL || semifinals->type != JSON_ARRAY || semifinals->item_count != 2 ||
        !find_pairing_for_players(semifinals, semifinal_seeds[0][0],
            semifinal_seeds[0][1], &first_match) ||
        !find_pairing_for_players(semifinals, semifinal_seeds[1][0],
            semifinal_seeds[1][1], &second_match)) {
        set_tournament_error("semifinal-pairings-invalid", "决赛登记缺少符合预赛 C 种子的半决赛配对");
        return 0;
    }
    if (!playoff_outcome(first_match, semifinal_seeds[0][0], semifinal_seeds[0][1],
            ranks, &semifinal_winners[0], &semifinal_losers[0]) ||
        !playoff_outcome(second_match, semifinal_seeds[1][0], semifinal_seeds[1][1],
            ranks, &semifinal_winners[1], &semifinal_losers[1])) {
        set_tournament_error("semifinal-results-incomplete", "两场半决赛都需要 PAPP C 确认的合法比分");
        return 0;
    }
    match = NULL;
    if (!find_pairing_for_players(pairings, semifinal_winners[0],
            semifinal_winners[1], &match)) {
        set_tournament_error("placement-pairings-invalid", "决赛配对与半决赛结果不一致");
        return 0;
    }
    phase_json = json_get(match, "phase");
    phase = json_text(phase_json);
    if (phase != NULL && strcmp(phase, "final") != 0) {
        set_tournament_error("placement-pairings-invalid", "决赛阶段标记无效");
        return 0;
    }
    third_match = NULL;
    if (!find_pairing_for_players(pairings, semifinal_losers[0],
            semifinal_losers[1], &third_match)) {
        set_tournament_error("placement-pairings-invalid", "三四名赛配对与半决赛结果不一致");
        return 0;
    }
    phase_json = json_get(third_match, "phase");
    phase = json_text(phase_json);
    if (phase != NULL && strcmp(phase, "third-place") != 0) {
        set_tournament_error("placement-pairings-invalid", "三四名赛阶段标记无效");
        return 0;
    }
    return 1;
}

static void write_playoff_pairing(long round_number, long table, const char *phase,
        long black_index, long white_index) {
    printf("{\"id\":");
    {
        char id[128];
        sprintf(id, "papp-c-%s-r%ld-t%ld", phase, round_number, table);
        write_json_string(id);
    }
    printf(",\"stage\":\"%s\",\"phase\":\"%s\",\"round\":%ld,\"table\":%ld,\"source\":\"papp-c\"",
        phase, phase, round_number, table);
    write_player_side("black", black_index);
    write_player_side("white", white_index);
    printf(",\"blackScore\":null,\"whiteScore\":null,\"status\":\"imported\"}");
}

static int operation_playoff_pairings(const JsonValue *root, const char *stage) {
    RoundProgress progress;
    JsonValue *semifinals;
    JsonValue *pairings_json;
    JsonValue *first_match;
    JsonValue *second_match;
    long order[TOURNAMENT_MAX_PLAYERS];
    long ranks[TOURNAMENT_MAX_PLAYERS];
    long round_number;
    long semifinal_winners[2];
    long semifinal_losers[2];
    long semifinal_seeds[2][2];
    int index;

    if (!playoffs_enabled(root)) {
        set_tournament_error("playoffs-disabled", "赛事参数未启用半决赛和决赛");
        return 0;
    }
    if (!configure_core(root)) return 0;
    progress = calculate_progress(root);
    if (!progress.complete) {
        set_tournament_error("preliminary-results-incomplete", "预赛配对或比分尚未全部由 PAPP C 确认");
        return 0;
    }
    if (tournament_player_count < 4) {
        set_tournament_error("playoff-players-missing", "半决赛至少需要 4 名已签到选手");
        return 0;
    }
    if (!load_preliminary_history(root, progress.expected_rounds, 1)) return 0;
    calculate_ranking(order, ranks);
    if (strcmp(stage, "semifinal") == 0) {
        round_number = progress.expected_rounds + 1;
        printf("{\"ok\":true,\"source\":\"papp-c\",\"operation\":\"pairings\",\"stage\":\"semifinal\",\"round\":%ld,\"pairings\":[",
            round_number);
        write_playoff_pairing(round_number, 1, "semifinal", order[0], order[3]);
        putchar(',');
        write_playoff_pairing(round_number, 2, "semifinal", order[1], order[2]);
        printf("],\"nextStage\":\"semifinal-score-registration\"}\n");
        return 1;
    }
    if (strcmp(stage, "placement") != 0) {
        set_tournament_error("unsupported-stage", "PAPP C 不支持此淘汰赛阶段");
        return 0;
    }
    semifinals = json_get(root, "semifinalPairings");
    if (semifinals == NULL || semifinals->type != JSON_ARRAY || semifinals->item_count != 2) {
        set_tournament_error("semifinal-pairings-missing", "缺少两场半决赛配对");
        return 0;
    }
    semifinal_seeds[0][0] = order[0];
    semifinal_seeds[0][1] = order[3];
    semifinal_seeds[1][0] = order[1];
    semifinal_seeds[1][1] = order[2];
    pairings_json = semifinals;
    first_match = NULL;
    second_match = NULL;
    if (!find_pairing_for_players(pairings_json, semifinal_seeds[0][0],
            semifinal_seeds[0][1], &first_match) ||
        !find_pairing_for_players(pairings_json, semifinal_seeds[1][0],
            semifinal_seeds[1][1], &second_match)) {
        set_tournament_error("semifinal-pairings-invalid", "半决赛配对与预赛 C 前四种子不一致");
        return 0;
    }
    if (!playoff_outcome(first_match, semifinal_seeds[0][0], semifinal_seeds[0][1],
            ranks, &semifinal_winners[0], &semifinal_losers[0]) ||
        !playoff_outcome(second_match, semifinal_seeds[1][0], semifinal_seeds[1][1],
            ranks, &semifinal_winners[1], &semifinal_losers[1])) {
        set_tournament_error("semifinal-results-incomplete", "两场半决赛都需要 PAPP C 确认的合法比分");
        return 0;
    }
    round_number = progress.expected_rounds + 2;
    printf("{\"ok\":true,\"source\":\"papp-c\",\"operation\":\"pairings\",\"stage\":\"placement\",\"round\":%ld,\"pairings\":[",
        round_number);
    write_playoff_pairing(round_number, 1, "final", semifinal_winners[0], semifinal_winners[1]);
    putchar(',');
    write_playoff_pairing(round_number, 2, "third-place", semifinal_losers[0], semifinal_losers[1]);
    printf("],\"nextStage\":\"placement-score-registration\"}\n");
    (void)index;
    return 1;
}

static int operation_standings(const JsonValue *root, int overall) {
    RoundProgress progress;
    long order[TOURNAMENT_MAX_PLAYERS];
    long ranks[TOURNAMENT_MAX_PLAYERS];
    long final_order[TOURNAMENT_MAX_PLAYERS];
    long overall_rank[TOURNAMENT_MAX_PLAYERS];
    long semifinal_winners[2];
    long semifinal_losers[2];
    long placement_winners[2];
    long placement_losers[2];
    long semifinal_seeds[2][2];
    long i;
    long count;
    int has_playoffs;
    JsonValue *semifinals;
    JsonValue *placements;
    JsonValue *match;
    const char *next_stage;

    if (!configure_core(root)) return 0;
    progress = calculate_progress(root);
    has_playoffs = playoffs_enabled(root);
    printf("{\"ok\":true,\"source\":\"papp-c\",\"operation\":%s,\"stage\":%s,\"mode\":%s,\"preliminaryRoundCount\":%ld,\"participantCount\":%ld,\"progress\":",
        overall ? "\"overall-standings\"" : "\"preliminary-standings\"",
        overall ? "\"overall\"" : "\"preliminary\"",
        overall && !has_playoffs ? "\"preliminary-only\"" : overall ? "\"playoff\"" : "\"preliminary\"",
        progress.expected_rounds, tournament_player_count);
    write_progress(&progress);
    printf(",\"hasSemifinalAndFinal\":%s,\"pointsUnit\":\"half-point-ticks\",\"standings\":[",
        has_playoffs ? "true" : "false");
    if (!progress.complete) {
        printf("],\"stageProgress\":{\"complete\":false},\"nextStage\":\"preliminary-registration\"}\n");
        return 1;
    }
    if (!load_preliminary_history(root, progress.expected_rounds, 1)) return 0;
    calculate_ranking(order, ranks);
    for (i = 0; i < tournament_player_count; i++) overall_rank[i] = ranks[i];
    if (!overall || !has_playoffs) {
        write_standings(order, ranks, overall ? overall_rank : NULL, has_playoffs);
        next_stage = overall ? "complete" : (has_playoffs ? "preliminary-ranking" : "overall-ranking");
        printf("],\"stageProgress\":{\"complete\":true},\"nextStage\":\"%s\"}\n", next_stage);
        return 1;
    }
    if (tournament_player_count < 4) {
        printf("],\"stageProgress\":{\"complete\":false},\"nextStage\":\"playoff-players-missing\"}\n");
        return 1;
    }
    semifinal_seeds[0][0] = order[0];
    semifinal_seeds[0][1] = order[3];
    semifinal_seeds[1][0] = order[1];
    semifinal_seeds[1][1] = order[2];
    semifinals = json_get(root, "semifinalPairings");
    if (semifinals == NULL && json_get(root, "playoffRegistration") != NULL)
        semifinals = json_get(json_get(root, "playoffRegistration"), "semifinalPairings");
    if (semifinals == NULL || semifinals->type != JSON_ARRAY || semifinals->item_count != 2) {
        printf("],\"stageProgress\":{\"complete\":false},\"nextStage\":\"semifinal-registration\"}\n");
        return 1;
    }
    count = 0;
    for (i = 0; i < 2; i++) {
        match = NULL;
        if (!find_pairing_for_players(semifinals, semifinal_seeds[i][0],
                semifinal_seeds[i][1], &match)) {
            set_tournament_error("semifinal-pairings-invalid", "半决赛配对与预赛 C 前四种子不一致");
            return 0;
        }
        if (!playoff_outcome(match, semifinal_seeds[i][0], semifinal_seeds[i][1],
                ranks, &semifinal_winners[i], &semifinal_losers[i])) {
            printf("],\"stageProgress\":{\"complete\":false},\"nextStage\":\"semifinal-score-registration\"}\n");
            return 1;
        }
        count++;
    }
    placements = json_get(root, "placementPairings");
    if (placements == NULL && json_get(root, "playoffRegistration") != NULL)
        placements = json_get(json_get(root, "playoffRegistration"), "placementPairings");
    if (placements == NULL || placements->type != JSON_ARRAY || placements->item_count != 2) {
        printf("],\"stageProgress\":{\"complete\":false},\"nextStage\":\"placement-registration\"}\n");
        return 1;
    }
    for (i = 0; i < 2; i++) {
        long first;
        long second;
        const char *expected_phase;
        expected_phase = i == 0 ? "final" : "third-place";
        first = i == 0 ? semifinal_winners[0] : semifinal_losers[0];
        second = i == 0 ? semifinal_winners[1] : semifinal_losers[1];
        match = NULL;
        if (!find_pairing_for_players(placements, first, second, &match) ||
            json_text(json_get(match, "phase")) == NULL ||
            strcmp(json_text(json_get(match, "phase")), expected_phase) != 0) {
            set_tournament_error("placement-pairings-invalid", "决赛和三四名赛配对与半决赛结果不一致");
            return 0;
        }
        if (!playoff_outcome(match, first, second, ranks,
                &placement_winners[i], &placement_losers[i])) {
            printf("],\"stageProgress\":{\"complete\":false},\"nextStage\":\"placement-score-registration\"}\n");
            return 1;
        }
    }
    final_order[0] = placement_winners[0];
    final_order[1] = placement_losers[0];
    final_order[2] = placement_winners[1];
    final_order[3] = placement_losers[1];
    for (i = 0; i < 4; i++) overall_rank[final_order[i]] = i + 1;
    count = 4;
    for (i = 0; i < tournament_player_count; i++) {
        if (ranks[order[i]] > 4) {
            final_order[count] = order[i];
            overall_rank[order[i]] = count + 1;
            count++;
        }
    }
    write_standings(final_order, ranks, overall_rank, 1);
    printf("],\"stageProgress\":{\"complete\":true},\"nextStage\":\"complete\"}\n");
    return 1;
}

static int operation_stage_status(const JsonValue *root) {
    const char *stage;
    JsonValue *stage_json;
    JsonValue *rounds_json;
    JsonValue *round_json;
    JsonValue *pairings_json;
    JsonValue *pairing_json;
    JsonValue *semifinals;
    JsonValue *placements;
    JsonValue *first_match;
    JsonValue *second_match;
    JsonValue *match;
    RoundProgress full_progress;
    long order[TOURNAMENT_MAX_PLAYERS];
    long ranks[TOURNAMENT_MAX_PLAYERS];
    long semifinal_seeds[2][2];
    long semifinal_winners[2];
    long semifinal_losers[2];
    long placement_winners[2];
    long placement_losers[2];
    long expected_rounds;
    long target_round;
    long rounds_with_pairings;
    long unresolved_pairings;
    long missing_count;
    long missing[NMAX_ROUNDS];
    long i;
    long j;
    long black_score;
    long white_score;
    int complete;

    if (!configure_core(root)) return 0;
    stage_json = json_get(root, "stage");
    stage = json_text(stage_json);
    if (stage == NULL) stage = "preliminary";

    if (strcmp(stage, "preliminary") == 0) {
        expected_rounds = number_of_rounds;
        target_round = 1;
        if (json_get(root, "round") != NULL &&
            !json_long_value(json_get(root, "round"), &target_round)) {
            set_tournament_error("invalid-round-index", "轮次推进状态需要有效轮次");
            return 0;
        }
        if (target_round < 1 || target_round > expected_rounds) {
            set_tournament_error("invalid-round-index", "目标轮次不在预赛轮数范围内");
            return 0;
        }
        rounds_json = json_get(root, "rounds");
        rounds_with_pairings = 0;
        unresolved_pairings = 0;
        missing_count = 0;
        for (i = 1; i <= target_round; i++) {
            round_json = find_round(rounds_json, i);
            pairings_json = json_get(round_json, "pairings");
            if (pairings_json == NULL || pairings_json->type != JSON_ARRAY ||
                    pairings_json->item_count == 0) {
                missing[missing_count++] = i;
                continue;
            }
            rounds_with_pairings++;
            for (j = 0; j < pairings_json->item_count; j++) {
                pairing_json = json_at(pairings_json, j);
                if (!round_is_bye(pairing_json) &&
                    !read_completed_pairing_score(pairing_json, &black_score, &white_score))
                    unresolved_pairings++;
            }
        }
        complete = missing_count == 0 && unresolved_pairings == 0;
        printf("{\"ok\":true,\"source\":\"papp-c\",\"operation\":\"stage-status\",\"stage\":\"preliminary\",\"round\":%ld,\"complete\":%s,\"canAdvance\":%s,\"nextStage\":\"%s\",\"progress\":{\"expectedRounds\":%ld,\"roundsChecked\":%ld,\"roundsWithPairings\":%ld,\"missingRounds\":[",
            target_round, complete ? "true" : "false", complete ? "true" : "false",
            complete ? (target_round < expected_rounds ? "preliminary-registration" :
                (playoffs_enabled(root) ? "preliminary-ranking" : "overall-ranking")) :
                "preliminary-registration",
            expected_rounds, target_round, rounds_with_pairings);
        for (i = 0; i < missing_count; i++) {
            if (i) putchar(',');
            printf("%ld", missing[i]);
        }
        printf("],\"unresolvedPairings\":%ld,\"complete\":%s}}\n",
            unresolved_pairings, complete ? "true" : "false");
        return 1;
    }

    if (strcmp(stage, "semifinal") != 0 && strcmp(stage, "placement") != 0) {
        set_tournament_error("unsupported-stage", "PAPP C 不支持此阶段状态查询");
        return 0;
    }
    if (!playoffs_enabled(root)) {
        set_tournament_error("playoffs-disabled", "赛事参数未启用半决赛和决赛");
        return 0;
    }
    full_progress = calculate_progress(root);
    if (!full_progress.complete) {
        printf("{\"ok\":true,\"source\":\"papp-c\",\"operation\":\"stage-status\",\"stage\":\"%s\",\"complete\":false,\"canAdvance\":false,\"nextStage\":\"preliminary-registration\",\"code\":\"preliminary-results-incomplete\",\"progress\":",
            stage);
        write_progress(&full_progress);
        printf("}\n");
        return 1;
    }
    if (tournament_player_count < 4) {
        printf("{\"ok\":true,\"source\":\"papp-c\",\"operation\":\"stage-status\",\"stage\":\"%s\",\"complete\":false,\"canAdvance\":false,\"nextStage\":\"playoff-players-missing\",\"code\":\"playoff-players-missing\"}\n",
            stage);
        return 1;
    }
    if (!load_preliminary_history(root, full_progress.expected_rounds, 1)) return 0;
    calculate_ranking(order, ranks);
    semifinal_seeds[0][0] = order[0];
    semifinal_seeds[0][1] = order[3];
    semifinal_seeds[1][0] = order[1];
    semifinal_seeds[1][1] = order[2];
    semifinals = json_get(root, "semifinalPairings");
    if (semifinals == NULL && json_get(root, "playoffRegistration") != NULL)
        semifinals = json_get(json_get(root, "playoffRegistration"), "semifinalPairings");
    if (semifinals == NULL || semifinals->type != JSON_ARRAY || semifinals->item_count != 2) {
        printf("{\"ok\":true,\"source\":\"papp-c\",\"operation\":\"stage-status\",\"stage\":\"%s\",\"complete\":false,\"canAdvance\":false,\"nextStage\":\"semifinal-registration\",\"code\":\"semifinal-pairings-missing\"}\n",
            stage);
        return 1;
    }
    first_match = NULL;
    second_match = NULL;
    if (!find_pairing_for_players(semifinals, semifinal_seeds[0][0],
            semifinal_seeds[0][1], &first_match) ||
        !find_pairing_for_players(semifinals, semifinal_seeds[1][0],
            semifinal_seeds[1][1], &second_match)) {
        set_tournament_error("semifinal-pairings-invalid", "半决赛配对与预赛 C 前四种子不一致");
        return 0;
    }
    if (!playoff_outcome(first_match, semifinal_seeds[0][0], semifinal_seeds[0][1],
            ranks, &semifinal_winners[0], &semifinal_losers[0]) ||
        !playoff_outcome(second_match, semifinal_seeds[1][0], semifinal_seeds[1][1],
            ranks, &semifinal_winners[1], &semifinal_losers[1])) {
        printf("{\"ok\":true,\"source\":\"papp-c\",\"operation\":\"stage-status\",\"stage\":\"%s\",\"complete\":false,\"canAdvance\":false,\"nextStage\":\"semifinal-score-registration\",\"code\":\"semifinal-results-incomplete\"}\n",
            stage);
        return 1;
    }
    if (strcmp(stage, "semifinal") == 0) {
        printf("{\"ok\":true,\"source\":\"papp-c\",\"operation\":\"stage-status\",\"stage\":\"semifinal\",\"complete\":true,\"canAdvance\":true,\"nextStage\":\"placement-registration\"}\n");
        return 1;
    }

    placements = json_get(root, "placementPairings");
    if (placements == NULL && json_get(root, "playoffRegistration") != NULL)
        placements = json_get(json_get(root, "playoffRegistration"), "placementPairings");
    if (placements == NULL || placements->type != JSON_ARRAY || placements->item_count != 2) {
        printf("{\"ok\":true,\"source\":\"papp-c\",\"operation\":\"stage-status\",\"stage\":\"placement\",\"complete\":false,\"canAdvance\":false,\"nextStage\":\"placement-registration\",\"code\":\"placement-pairings-missing\"}\n");
        return 1;
    }
    for (i = 0; i < 2; i++) {
        long first_player;
        long second_player;
        const char *expected_phase;
        expected_phase = i == 0 ? "final" : "third-place";
        first_player = i == 0 ? semifinal_winners[0] : semifinal_losers[0];
        second_player = i == 0 ? semifinal_winners[1] : semifinal_losers[1];
        match = NULL;
        if (!find_pairing_for_players(placements, first_player, second_player, &match) ||
            json_text(json_get(match, "phase")) == NULL ||
            strcmp(json_text(json_get(match, "phase")), expected_phase) != 0) {
            set_tournament_error("placement-pairings-invalid", "决赛和三四名赛配对与半决赛结果不一致");
            return 0;
        }
        if (!playoff_outcome(match, first_player, second_player, ranks,
                &placement_winners[i], &placement_losers[i])) {
            printf("{\"ok\":true,\"source\":\"papp-c\",\"operation\":\"stage-status\",\"stage\":\"placement\",\"complete\":false,\"canAdvance\":false,\"nextStage\":\"placement-score-registration\",\"code\":\"placement-results-incomplete\"}\n");
            return 1;
        }
    }
    printf("{\"ok\":true,\"source\":\"papp-c\",\"operation\":\"stage-status\",\"stage\":\"placement\",\"complete\":true,\"canAdvance\":true,\"nextStage\":\"overall-ranking\"}\n");
    return 1;
}

static long count_complete_history_prefix(const JsonValue *root) {
    JsonValue *rounds_json;
    JsonValue *round_json;
    long round_number;
    rounds_json = json_get(root, "rounds");
    for (round_number = 1; round_number <= NMAX_ROUNDS; round_number++) {
        round_json = find_round(rounds_json, round_number);
        if (!history_round_complete(round_json)) return round_number - 1;
    }
    return NMAX_ROUNDS;
}

static void write_validated_pairing(const JsonValue *pairing, long table_index) {
    const char *id;
    const char *status;
    const char *phase;
    JsonValue *table_json;
    long black_score;
    long white_score;
    long black_index;
    long white_index;
    long bye_index;

    id = json_identity(json_get_any(pairing, "id", "pairingId", "sourceLocalId"));
    status = json_text(json_get(pairing, "status"));
    phase = json_text(json_get(pairing, "phase"));
    table_json = json_get(pairing, "table");
    printf("{\"id\":");
    write_nullable_string(id);
    printf(",\"table\":");
    if (table_json != NULL) write_json_value(table_json);
    else printf("%ld", table_index);
    printf(",\"source\":\"papp-c\"");
    if (phase != NULL) {
        printf(",\"phase\":");
        write_json_string(phase);
    }
    if (round_is_bye(pairing)) {
        bye_index = player_id_from_pairing(pairing, 1, 1);
        printf(",\"playerId\":");
        write_json_string(tournament_players[bye_index].id);
        printf(",\"black\":");
        write_json_string(tournament_players[bye_index].name);
        printf(",\"blackName\":");
        write_json_string(tournament_players[bye_index].name);
        printf(",\"white\":\"BYE\",\"whiteName\":\"BYE\"");
        printf(",\"blackId\":");
        write_json_string(tournament_players[bye_index].id);
        printf(",\"blackAccount\":");
        write_nullable_string(tournament_players[bye_index].account);
        printf(",\"blackScore\":40,\"whiteScore\":24,\"status\":\"bye\",\"pointsHalfUnits\":2,\"displayPoints\":1,\"discs\":40}");
        return;
    }
    black_index = player_id_from_pairing(pairing, 0, 1);
    white_index = player_id_from_pairing(pairing, 0, 0);
    write_player_side("black", black_index);
    write_player_side("white", white_index);
    if (read_pairing_score(pairing, &black_score, &white_score))
        printf(",\"blackScore\":%ld,\"whiteScore\":%ld", black_score, white_score);
    else printf(",\"blackScore\":null,\"whiteScore\":null");
    printf(",\"status\":");
    write_json_string(status == NULL ? "imported" : status);
    printf("}");
}

static int operation_validate_pairings(const JsonValue *root) {
    JsonValue *pairings;
    JsonValue *pairing;
    JsonValue *present_json;
    JsonValue *score_json;
    long seen[TOURNAMENT_MAX_PLAYERS];
    long present[TOURNAMENT_MAX_PLAYERS];
    long i;
    long black_index;
    long white_index;
    long bye_index;
    long black_score;
    long white_score;
    long requested_round;
    JsonValue *stage_json;
    const char *stage;
    int has_requested_round;
    int has_presence;
    if (!configure_core(root)) return 0;
    stage_json = json_get(root, "stage");
    stage = json_text(stage_json);
    has_requested_round = json_long_value(json_get(root, "round"), &requested_round);
    if (stage == NULL && has_requested_round && requested_round == number_of_rounds + 1)
        stage = "semifinal";
    else if (stage == NULL && has_requested_round && requested_round == number_of_rounds + 2)
        stage = "placement";
    else if (stage == NULL) stage = "preliminary";
    if (stage != NULL && strcmp(stage, "semifinal") == 0 && has_requested_round &&
            requested_round != number_of_rounds + 1) {
        set_tournament_error("invalid-round-index", "半决赛导入轮次与预赛轮数不一致");
        return 0;
    }
    if (stage != NULL && strcmp(stage, "placement") == 0 && has_requested_round &&
            requested_round != number_of_rounds + 2) {
        set_tournament_error("invalid-round-index", "决赛导入轮次与预赛轮数不一致");
        return 0;
    }
    if (has_requested_round && requested_round > number_of_rounds + 2) {
        set_tournament_error("invalid-round-index", "目标轮次超出 PAPP C 支持范围");
        return 0;
    }
    if (has_requested_round && requested_round > number_of_rounds && !playoffs_enabled(root)) {
        set_tournament_error("playoffs-disabled", "赛事未启用半决赛和决赛，不能导入预赛之后的配对");
        return 0;
    }
    if ((stage != NULL && (strcmp(stage, "semifinal") == 0 || strcmp(stage, "placement") == 0)) &&
            !playoffs_enabled(root)) {
        set_tournament_error("playoffs-disabled", "赛事未启用半决赛和决赛，不能导入预赛之后的配对");
        return 0;
    }
    pairings = json_get(root, "pairings");
    if (pairings == NULL || pairings->type != JSON_ARRAY || pairings->item_count == 0) {
        set_tournament_error("pairings-empty", "要导入的配对列表不能为空");
        return 0;
    }
    present_json = json_get(root, "presentPlayerIds");
    has_presence = present_json != NULL && present_json->type != JSON_NULL &&
        strcmp(stage, "semifinal") != 0 && strcmp(stage, "placement") != 0;
    memset(seen, 0, sizeof(seen));
    memset(present, 0, sizeof(present));
    if (has_presence) {
        if (present_json->type != JSON_ARRAY) {
            set_tournament_error("invalid-presence", "presentPlayerIds 必须是选手 id 列表");
            return 0;
        }
        for (i = 0; i < present_json->item_count; i++) {
            long index;
            const char *id;
            id = json_identity(json_at(present_json, i));
            index = find_player_index(id);
            if (index < 0 || present[index]) {
                set_tournament_error("invalid-presence", "签到名单包含未知或重复选手 id");
                return 0;
            }
            present[index] = 1;
        }
    }
    for (i = 0; i < pairings->item_count; i++) {
        pairing = json_at(pairings, i);
        if (!round_is_bye(pairing)) {
            score_json = json_get(pairing, "blackScore");
            if (score_json != NULL && score_json->type != JSON_NULL) {
                if (!read_pairing_score(pairing, &black_score, &white_score)) {
                    set_tournament_error("invalid-score-pair", "导入的配对比分必须是合法的 64 子比分");
                    return 0;
                }
            } else {
                score_json = json_get(pairing, "whiteScore");
                if (score_json != NULL && score_json->type != JSON_NULL) {
                    set_tournament_error("invalid-score-pair", "导入的配对必须同时提供黑白双方比分");
                    return 0;
                }
            }
        }
        if (round_is_bye(pairing)) {
            bye_index = player_id_from_pairing(pairing, 1, 1);
            if (bye_index < 0 || seen[bye_index]) {
                set_tournament_error("invalid-bye", "导入的轮空记录重复或引用未知选手");
                return 0;
            }
            seen[bye_index] = 1;
        } else {
            black_index = player_id_from_pairing(pairing, 0, 1);
            white_index = player_id_from_pairing(pairing, 0, 0);
            if (black_index < 0 || white_index < 0 || black_index == white_index ||
                seen[black_index] || seen[white_index]) {
                set_tournament_error("invalid-pairing", "导入配对包含重复选手或无效选手 id");
                return 0;
            }
            seen[black_index] = 1;
            seen[white_index] = 1;
        }
    }
    if (has_presence) {
        for (i = 0; i < tournament_player_count; i++) {
            if (present[i] != seen[i]) {
                set_tournament_error("pairing-presence-mismatch", "导入配对必须恰好覆盖本轮已签到选手");
                return 0;
            }
        }
    }
    if (strcmp(stage, "semifinal") == 0 || strcmp(stage, "placement") == 0) {
        if (!validate_playoff_pairing_set(root, pairings, stage)) return 0;
    } else if (has_requested_round && requested_round > number_of_rounds) {
        set_tournament_error("invalid-round-index", "预赛配对导入轮次超出预赛范围");
        return 0;
    }
    printf("{\"ok\":true,\"source\":\"papp-c\",\"operation\":\"validate-pairings\",\"validationSource\":\"papp-c\",\"pairings\":[");
    for (i = 0; i < pairings->item_count; i++) {
        if (i) putchar(',');
        write_validated_pairing(json_at(pairings, i), i + 1);
    }
    printf("]}\n");
    return 1;
}

static int operation_pairings(const JsonValue *root) {
    JsonValue *round_json;
    JsonValue *present_json;
    JsonValue *stage_json;
    const char *stage;
    long round_number;
    long history_count;
    long rank_by_player[TOURNAMENT_MAX_PLAYERS];
    long order[TOURNAMENT_MAX_PLAYERS];
    long i;
    stage_json = json_get(root, "stage");
    stage = json_text(stage_json);
    if (stage == NULL) stage = "preliminary";
    if (strcmp(stage, "semifinal") == 0 || strcmp(stage, "placement") == 0)
        return operation_playoff_pairings(root, stage);
    if (!configure_core(root)) return 0;
    if (strcmp(stage, "preliminary") != 0) {
        set_tournament_error("unsupported-stage", "PAPP C 不支持此比赛阶段");
        return 0;
    }
    round_number = 0;
    if (!json_long_value(json_get_any(root, "roundIndex", "round", NULL), &round_number))
        round_number = count_complete_history_prefix(root) + 1;
    if (round_number < 1 || round_number > number_of_rounds) {
        set_tournament_error("invalid-round-index", "目标轮次不在预赛轮数范围内");
        return 0;
    }
    history_count = round_number - 1;
    if (count_complete_history_prefix(root) < history_count) {
        set_tournament_error("preliminary-incomplete", "必须先由 PAPP C 确认前序预赛轮次完整");
        return 0;
    }
    if (!load_preliminary_history(root, history_count, 1)) return 0;
    present_json = json_get(root, "presentPlayerIds");
    if (!set_presence_from_ids(present_json, 1)) return 0;
    if (history_count > 0) {
        calculate_ranking(order, rank_by_player);
    } else {
        for (i = 0; i < tournament_player_count; i++) rank_by_player[i] = i + 1;
    }
    compute_pairings();
    printf("{\"ok\":true,\"source\":\"papp-c\",\"operation\":\"pairings\",\"stage\":\"preliminary\",\"round\":%ld,\"preliminaryRoundCount\":%ld,\"participantCount\":%ld,\"pairings\":",
        round_number, number_of_rounds, tournament_player_count);
    write_current_pairings("preliminary", round_number, rank_by_player, "papp-c");
    printf(",\"progress\":");
    round_json = json_get(root, "rounds");
    if (round_json == NULL) printf("{\"expectedRounds\":%ld,\"roundsWithPairings\":0,\"missingRounds\":[1],\"unresolvedPairings\":0,\"complete\":false}", number_of_rounds);
    else {
        RoundProgress progress;
        progress = calculate_progress(root);
        write_progress(&progress);
    }
    printf("}\n");
    return 1;
}

#define PAPP_SCORE_WORKFILE_MARKER "# PAPP-TOURNAMENT-ADAPTER-WORKFILE-V1"
#define PAPP_SCORE_BATCH_MARKER "# PAPP-TOURNAMENT-SCORE-BATCH-V1 "
#define PAPP_SCORE_BATCH_MAX_PAIRINGS (TOURNAMENT_MAX_PLAYERS / 2)

typedef struct {
    const char *id;
    long table;
    const char *black_id;
    const char *white_id;
    const char *black;
    const char *white;
    const char *black_account;
    const char *white_account;
    const char *oq_game_id;
    long black_papp_id;
    long white_papp_id;
    long black_score;
    long white_score;
} ScoreBatchPairing;

typedef struct {
    const char *batch_id;
    const char *stage;
    const char *tournament_name;
    long round;
    long preliminary_round_count;
    long has_playoffs;
    long native_round;
    long native_round_count;
    long pairing_count;
    ScoreBatchPairing *pairings;
    JsonValue *json;
    const char *raw_json;
} ScoreBatchRecord;

static const char *score_text(const JsonValue *value) {
    const char *text;
    text = json_text(value);
    return text == NULL ? "" : text;
}

static int score_text_equal(const char *left, const char *right) {
    if (left == NULL) left = "";
    if (right == NULL) right = "";
    return strcmp(left, right) == 0;
}

static int score_batch_native_round(const char *stage, long round,
        long preliminary_round_count, long has_playoffs,
        long *native_round, long *native_round_count) {
    long total;
    if (preliminary_round_count < 1 || preliminary_round_count > NMAX_ROUNDS) return 0;
    total = preliminary_round_count + (has_playoffs ? 2 : 0);
    if (total > NMAX_ROUNDS) return 0;
    if (stage != NULL && strcmp(stage, "preliminary") == 0) {
        if (round < 1 || round > preliminary_round_count) return 0;
        *native_round = round;
    } else if (stage != NULL && strcmp(stage, "semifinal") == 0) {
        if (!has_playoffs || round != preliminary_round_count + 1) return 0;
        *native_round = preliminary_round_count + 1;
    } else if (stage != NULL && strcmp(stage, "placement") == 0) {
        if (!has_playoffs || round != preliminary_round_count + 2) return 0;
        *native_round = preliminary_round_count + 2;
    } else {
        return 0;
    }
    *native_round_count = total;
    return 1;
}

static long papp_id_for_player_index(long index) {
    if (index < 0 || index >= tournament_player_count) return 0;
    return tournament_players[index].papp_id;
}

static int score_batch_record_from_json(JsonValue *json, ScoreBatchRecord *record,
        int stored_record) {
    JsonValue *pairings_json;
    JsonValue *pairing_json;
    const char *stage;
    const char *id;
    const char *black_id;
    const char *white_id;
    long black_score;
    long white_score;
    long table;
    long round;
    long preliminary_round_count;
    long declared_native_round;
    long declared_native_round_count;
    long black_index;
    long white_index;
    long i;
    long j;
    long used_players[TOURNAMENT_MAX_PLAYERS];

    memset(record, 0, sizeof(*record));
    record->json = json;
    record->batch_id = json_identity(json_get(json, "batchId"));
    stage = json_text(json_get(json, "stage"));
    record->stage = stage;
    record->tournament_name = score_text(json_get(json, "tournamentName"));
    if (record->batch_id == NULL || *record->batch_id == '\0' ||
        !json_long_value(json_get(json, "round"), &round) || round < 1 ||
        !json_long_value(json_get_any(json, "preliminaryRoundCount", "roundCount", NULL),
            &preliminary_round_count)) {
        set_tournament_error("invalid-score-batch", "比分批次缺少 batchId、轮次或预赛轮数");
        return 0;
    }
    record->round = round;
    record->preliminary_round_count = preliminary_round_count;
    record->has_playoffs = json_boolean_value(
        json_get(json, "hasSemifinalAndFinal"), 1);
    if (!score_batch_native_round(stage, round, preliminary_round_count,
            record->has_playoffs, &record->native_round,
            &record->native_round_count)) {
        set_tournament_error("invalid-score-stage-round", "比分阶段与 PAPP workfile 轮次不匹配");
        return 0;
    }
    if (stored_record) {
        if (!json_long_value(json_get(json, "nativeRound"), &declared_native_round) ||
            !json_long_value(json_get(json, "nativeRoundCount"), &declared_native_round_count) ||
            declared_native_round != record->native_round ||
            declared_native_round_count != record->native_round_count) {
            set_tournament_error("invalid-score-batch", "PAPP workfile 中的批次轮次元数据无效");
            return 0;
        }
    }

    pairings_json = json_get(json, "pairings");
    if (pairings_json == NULL || pairings_json->type != JSON_ARRAY ||
        pairings_json->item_count < 1 ||
        pairings_json->item_count > PAPP_SCORE_BATCH_MAX_PAIRINGS) {
        set_tournament_error("invalid-score-batch", "比分批次配对数量无效");
        return 0;
    }
    record->pairings = (ScoreBatchPairing *)calloc(
        (size_t)pairings_json->item_count, sizeof(ScoreBatchPairing));
    if (record->pairings == NULL) {
        set_tournament_error("out-of-memory", "PAPP C 无法读取比分批次");
        return 0;
    }
    record->pairing_count = pairings_json->item_count;
    memset(used_players, 0, sizeof(used_players));
    for (i = 0; i < record->pairing_count; i++) {
        pairing_json = json_at(pairings_json, i);
        id = json_identity(json_get_any(pairing_json, "id", "pairingId", "sourceLocalId"));
        black_id = json_identity(json_get_any(pairing_json, "blackId", "blackPlayerId", NULL));
        white_id = json_identity(json_get_any(pairing_json, "whiteId", "whitePlayerId", NULL));
        if (id == NULL || *id == '\0' || black_id == NULL || *black_id == '\0' ||
            white_id == NULL || *white_id == '\0' || strcmp(black_id, white_id) == 0 ||
            !json_long_value(json_get(pairing_json, "table"), &table) || table < 1 ||
            !read_pairing_score(pairing_json, &black_score, &white_score)) {
            set_tournament_error("invalid-score-pair", "比分批次必须包含稳定配对 id、黑白选手、桌号及和为 64 的整数比分");
            return 0;
        }
        black_index = find_player_index(black_id);
        white_index = find_player_index(white_id);
        if (black_index < 0 || white_index < 0 || black_index == white_index ||
            used_players[black_index] || used_players[white_index]) {
            set_tournament_error("invalid-score-identity", "比分批次存在未知、重复或相同的黑白选手");
            return 0;
        }
        used_players[black_index] = 1;
        used_players[white_index] = 1;
        for (j = 0; j < i; j++) {
            if (strcmp(record->pairings[j].id, id) == 0 ||
                record->pairings[j].table == table) {
                set_tournament_error("duplicate-score-pairing", "比分批次包含重复配对 id 或桌号");
                return 0;
            }
        }
        record->pairings[i].id = id;
        record->pairings[i].table = table;
        record->pairings[i].black_id = black_id;
        record->pairings[i].white_id = white_id;
        record->pairings[i].black = score_text(json_get(pairing_json, "black"));
        record->pairings[i].white = score_text(json_get(pairing_json, "white"));
        record->pairings[i].black_account = score_text(json_get(pairing_json, "blackAccount"));
        record->pairings[i].white_account = score_text(json_get(pairing_json, "whiteAccount"));
        record->pairings[i].oq_game_id = score_text(json_get(pairing_json, "oqGameId"));
        if (record->pairings[i].black[0] == '\0' || record->pairings[i].white[0] == '\0' ||
            !score_text_equal(record->pairings[i].black, tournament_players[black_index].name) ||
            !score_text_equal(record->pairings[i].white, tournament_players[white_index].name) ||
            !score_text_equal(record->pairings[i].black_account,
                tournament_players[black_index].account) ||
            !score_text_equal(record->pairings[i].white_account,
                tournament_players[white_index].account)) {
            set_tournament_error("invalid-score-identity", "比分批次黑白姓名或账号与 PAPP 选手身份不一致");
            return 0;
        }
        if (stored_record) {
            if (!json_long_value(json_get(pairing_json, "blackPappId"), &declared_native_round) ||
                !json_long_value(json_get(pairing_json, "whitePappId"), &declared_native_round_count) ||
                declared_native_round < 1 || declared_native_round_count < 1) {
                set_tournament_error("invalid-score-batch", "PAPP workfile 中的原生选手编号无效");
                return 0;
            }
            record->pairings[i].black_papp_id = declared_native_round;
            record->pairings[i].white_papp_id = declared_native_round_count;
        } else {
            record->pairings[i].black_papp_id = papp_id_for_player_index(black_index);
            record->pairings[i].white_papp_id = papp_id_for_player_index(white_index);
        }
        record->pairings[i].black_score = black_score;
        record->pairings[i].white_score = white_score;
    }
    if (stored_record && json_get(json, "playerMap") == NULL) {
        set_tournament_error("invalid-score-batch", "PAPP workfile 缺少原生选手编号映射");
        return 0;
    }
    return 1;
}

static int score_batch_player_map_matches(const ScoreBatchRecord *record) {
    JsonValue *map;
    JsonValue *row;
    const char *id;
    long papp_id;
    long i;
    map = json_get(record->json, "playerMap");
    if (map == NULL || map->type != JSON_ARRAY || map->item_count != tournament_player_count)
        return 0;
    for (i = 0; i < tournament_player_count; i++) {
        row = json_at(map, i);
        id = json_identity(json_get(row, "id"));
        if (!json_long_value(json_get(row, "pappId"), &papp_id) ||
            id == NULL || strcmp(id, tournament_players[i].id) != 0 ||
            papp_id != tournament_players[i].papp_id) return 0;
    }
    return 1;
}

static int score_batch_context_matches(const ScoreBatchRecord *record,
        const ScoreBatchRecord *context) {
    return score_text_equal(record->tournament_name, context->tournament_name) &&
        record->preliminary_round_count == context->preliminary_round_count &&
        record->has_playoffs == context->has_playoffs &&
        record->native_round_count == context->native_round_count;
}

static int score_pairing_identity_equal(const ScoreBatchPairing *left,
        const ScoreBatchPairing *right) {
    return score_text_equal(left->black_id, right->black_id) &&
        score_text_equal(left->white_id, right->white_id) &&
        score_text_equal(left->black, right->black) &&
        score_text_equal(left->white, right->white) &&
        score_text_equal(left->black_account, right->black_account) &&
        score_text_equal(left->white_account, right->white_account) &&
        left->black_papp_id == right->black_papp_id &&
        left->white_papp_id == right->white_papp_id;
}

static ScoreBatchPairing *score_batch_find_pairing(ScoreBatchRecord *record,
        const char *id) {
    long i;
    if (record == NULL || id == NULL) return NULL;
    for (i = 0; i < record->pairing_count; i++)
        if (strcmp(record->pairings[i].id, id) == 0) return &record->pairings[i];
    return NULL;
}

static ScoreBatchRecord *score_batch_find_record(ScoreBatchRecord *records,
        long record_count, const char *batch_id) {
    long i;
    for (i = 0; i < record_count; i++)
        if (score_text_equal(records[i].batch_id, batch_id)) return &records[i];
    return NULL;
}

static long score_batch_last_native_round(const ScoreBatchRecord *records,
        long record_count) {
    long last_round;
    long i;
    last_round = 0;
    for (i = 0; i < record_count; i++)
        if (records[i].native_round > last_round)
            last_round = records[i].native_round;
    return last_round;
}

static int score_batch_records_equal(const ScoreBatchRecord *left,
        const ScoreBatchRecord *right) {
    long i;
    ScoreBatchPairing *match;
    if (!score_text_equal(left->stage, right->stage) || left->round != right->round ||
        !score_batch_context_matches(left, right) ||
        left->pairing_count != right->pairing_count) return 0;
    for (i = 0; i < right->pairing_count; i++) {
        match = score_batch_find_pairing((ScoreBatchRecord *)left, right->pairings[i].id);
        if (match == NULL || match->table != right->pairings[i].table ||
            !score_pairing_identity_equal(match, &right->pairings[i]) ||
            !score_text_equal(match->oq_game_id, right->pairings[i].oq_game_id) ||
            match->black_score != right->pairings[i].black_score ||
            match->white_score != right->pairings[i].white_score) return 0;
    }
    return 1;
}

static int build_score_round_snapshot(ScoreBatchRecord *records, long record_count,
        long native_round, ScoreBatchPairing *active, long *active_count) {
    ScoreBatchPairing *stored;
    long i;
    long j;
    long k;
    long black_papp_id;
    long white_papp_id;
    *active_count = 0;
    for (i = 0; i < record_count; i++) {
        if (records[i].native_round != native_round) continue;
        for (j = 0; j < records[i].pairing_count; j++) {
            stored = &records[i].pairings[j];
            for (k = 0; k < *active_count; k++)
                if (strcmp(active[k].id, stored->id) == 0) break;
            if (k < *active_count) {
                if (!score_pairing_identity_equal(&active[k], stored)) {
                    set_tournament_error("score-identity-conflict", "相同配对 id 的 PAPP 原生选手身份或黑白方向不一致");
                    return 0;
                }
                active[k] = *stored;
            } else {
                if (*active_count >= PAPP_SCORE_BATCH_MAX_PAIRINGS) {
                    set_tournament_error("score-round-too-large", "PAPP workfile 单轮比分配对数量超出上限");
                    return 0;
                }
                active[*active_count] = *stored;
                (*active_count)++;
            }
        }
    }
    for (i = 0; i < *active_count; i++) {
        for (j = 0; j < i; j++) {
            black_papp_id = active[i].black_papp_id;
            white_papp_id = active[i].white_papp_id;
            if (black_papp_id == active[j].black_papp_id ||
                black_papp_id == active[j].white_papp_id ||
                white_papp_id == active[j].black_papp_id ||
                white_papp_id == active[j].white_papp_id) {
                set_tournament_error("duplicate-native-pairing-player", "PAPP workfile 同一轮重复安排了选手");
                return 0;
            }
        }
    }
    return 1;
}

static char *read_score_workfile_text(const char *filename, long *length,
        int *exists) {
    FILE *fp;
    char *buffer;
    char *resized;
    long used;
    long capacity;
    int current;
    *exists = 0;
    *length = 0;
    errno = 0;
    fp = fopen(filename, "rb");
    if (fp == NULL) {
        if (errno == ENOENT) return NULL;
        set_tournament_error("native-workfile-open-failed", "无法读取 PAPP workfile");
        return NULL;
    }
    *exists = 1;
    capacity = 4096;
    used = 0;
    buffer = (char *)malloc((size_t)capacity + 1);
    if (buffer == NULL) {
        fclose(fp);
        set_tournament_error("out-of-memory", "PAPP C 无法读取 workfile");
        return NULL;
    }
    while ((current = fgetc(fp)) != EOF) {
        if (used >= JSON_INPUT_LIMIT) {
            fclose(fp);
            free(buffer);
            set_tournament_error("native-workfile-too-large", "PAPP workfile 超出读取上限");
            return NULL;
        }
        if (used == capacity) {
            capacity *= 2;
            if (capacity > JSON_INPUT_LIMIT) capacity = JSON_INPUT_LIMIT;
            resized = (char *)realloc(buffer, (size_t)capacity + 1);
            if (resized == NULL) {
                fclose(fp);
                free(buffer);
                set_tournament_error("out-of-memory", "PAPP C 无法读取 workfile");
                return NULL;
            }
            buffer = resized;
        }
        buffer[used++] = (char)current;
    }
    if (ferror(fp)) {
        fclose(fp);
        free(buffer);
        set_tournament_error("native-workfile-read-failed", "读取 PAPP workfile 时发生错误");
        return NULL;
    }
    fclose(fp);
    buffer[used] = '\0';
    *length = used;
    return buffer;
}

static int load_score_workfile(const char *filename, ScoreBatchRecord **records_out,
        long *record_count_out, char **contents_out, int *exists_out) {
    char *contents;
    char *line;
    char *line_end;
    char *cursor;
    char *metadata;
    JsonValue *json;
    const char *parse_error;
    ScoreBatchRecord parsed;
    ScoreBatchRecord *records;
    ScoreBatchRecord *resized;
    long length;
    long record_count;
    long i;
    int exists;
    int has_workfile_marker;
    int has_batch_line;

    contents = read_score_workfile_text(filename, &length, &exists);
    *records_out = NULL;
    *record_count_out = 0;
    *contents_out = contents;
    *exists_out = exists;
    if (!exists) return 1;
    if (contents == NULL) return 0;

    has_workfile_marker = 0;
    has_batch_line = 0;
    records = NULL;
    record_count = 0;
    cursor = contents;
    while (cursor < contents + length) {
        line = cursor;
        line_end = strchr(cursor, '\n');
        if (line_end != NULL) {
            *line_end = '\0';
            cursor = line_end + 1;
        } else {
            cursor = contents + length;
        }
        if (line[0] != '\0' && line[strlen(line) - 1] == '\r')
            line[strlen(line) - 1] = '\0';
        if (strcmp(line, PAPP_SCORE_WORKFILE_MARKER) == 0)
            has_workfile_marker = 1;
        if (strncmp(line, PAPP_SCORE_BATCH_MARKER,
                strlen(PAPP_SCORE_BATCH_MARKER)) != 0) continue;
        has_batch_line = 1;
        metadata = line + strlen(PAPP_SCORE_BATCH_MARKER);
        json = parse_json_document(metadata, strlen(metadata), &parse_error);
        if (json == NULL || json->type != JSON_OBJECT) {
            set_tournament_error("native-workfile-invalid", "PAPP workfile 中的比分批次元数据无效");
            free(records);
            return 0;
        }
        memset(&parsed, 0, sizeof(parsed));
        if (!score_batch_record_from_json(json, &parsed, 1)) {
            free(records);
            return 0;
        }
        parsed.raw_json = copy_range(metadata, strlen(metadata));
        if (parsed.raw_json == NULL) {
            set_tournament_error("out-of-memory", "PAPP C 无法保留 workfile 批次元数据");
            free(records);
            return 0;
        }
        for (i = 0; i < record_count; i++) {
            if (score_text_equal(records[i].batch_id, parsed.batch_id)) {
                set_tournament_error("duplicate-persisted-batch-id", "PAPP workfile 中存在重复 batchId");
                free(records);
                return 0;
            }
        }
        resized = (ScoreBatchRecord *)realloc(records,
            (size_t)(record_count + 1) * sizeof(ScoreBatchRecord));
        if (resized == NULL) {
            set_tournament_error("out-of-memory", "PAPP C 无法读取 workfile 批次");
            free(records);
            return 0;
        }
        records = resized;
        records[record_count++] = parsed;
    }
    if (!has_workfile_marker || !has_batch_line) {
        set_tournament_error("unmanaged-native-workfile", "现有 PAPP workfile 不是此适配器创建的比分存储，未作修改");
        free(records);
        return 0;
    }
    *records_out = records;
    *record_count_out = record_count;
    return 1;
}

static void write_file_json_string(FILE *fp, const char *value) {
    const unsigned char *cursor;
    fputc('"', fp);
    if (value != NULL) {
        for (cursor = (const unsigned char *)value; *cursor; cursor++) {
            if (*cursor == '"') fputs("\\\"", fp);
            else if (*cursor == '\\') fputs("\\\\", fp);
            else if (*cursor == '\b') fputs("\\b", fp);
            else if (*cursor == '\f') fputs("\\f", fp);
            else if (*cursor == '\n') fputs("\\n", fp);
            else if (*cursor == '\r') fputs("\\r", fp);
            else if (*cursor == '\t') fputs("\\t", fp);
            else if (*cursor < 0x20) fprintf(fp, "\\u%04x", (unsigned int)*cursor);
            else fputc((int)*cursor, fp);
        }
    }
    fputc('"', fp);
}

static int write_score_batch_metadata(FILE *fp, const ScoreBatchRecord *record) {
    long i;
    fputs(PAPP_SCORE_BATCH_MARKER, fp);
    fputs("{\"batchId\":", fp);
    write_file_json_string(fp, record->batch_id);
    fputs(",\"stage\":", fp);
    write_file_json_string(fp, record->stage);
    fprintf(fp, ",\"round\":%ld,\"preliminaryRoundCount\":%ld,\"hasSemifinalAndFinal\":%s,\"nativeRound\":%ld,\"nativeRoundCount\":%ld,\"tournamentName\":",
        record->round, record->preliminary_round_count,
        record->has_playoffs ? "true" : "false",
        record->native_round, record->native_round_count);
    write_file_json_string(fp, record->tournament_name);
    fputs(",\"playerMap\":[", fp);
    for (i = 0; i < tournament_player_count; i++) {
        if (i) fputc(',', fp);
        fputs("{\"id\":", fp);
        write_file_json_string(fp, tournament_players[i].id);
        fprintf(fp, ",\"pappId\":%ld}", tournament_players[i].papp_id);
    }
    fputs("],\"pairings\":[", fp);
    for (i = 0; i < record->pairing_count; i++) {
        const ScoreBatchPairing *pairing = &record->pairings[i];
        if (i) fputc(',', fp);
        fputs("{\"id\":", fp);
        write_file_json_string(fp, pairing->id);
        fprintf(fp, ",\"table\":%ld,\"blackId\":", pairing->table);
        write_file_json_string(fp, pairing->black_id);
        fputs(",\"whiteId\":", fp);
        write_file_json_string(fp, pairing->white_id);
        fputs(",\"black\":", fp);
        write_file_json_string(fp, pairing->black);
        fputs(",\"white\":", fp);
        write_file_json_string(fp, pairing->white);
        fputs(",\"blackAccount\":", fp);
        write_file_json_string(fp, pairing->black_account);
        fputs(",\"whiteAccount\":", fp);
        write_file_json_string(fp, pairing->white_account);
        fputs(",\"oqGameId\":", fp);
        write_file_json_string(fp, pairing->oq_game_id);
        fprintf(fp, ",\"blackPappId\":%ld,\"whitePappId\":%ld,\"blackScore\":%ld,\"whiteScore\":%ld}",
            pairing->black_papp_id, pairing->white_papp_id,
            pairing->black_score, pairing->white_score);
    }
    fputs("]}\n", fp);
    return ferror(fp) == 0;
}

static int verify_score_round_in_papp_core(ScoreBatchRecord *records,
        long record_count, long native_round) {
    ScoreBatchPairing expected[PAPP_SCORE_BATCH_MAX_PAIRINGS];
    long expected_count;
    long n1;
    long n2;
    long i;
    long found;
    long found_count;
    long black_score;
    double native_score;
    discs_t value;
    int verified[PAPP_SCORE_BATCH_MAX_PAIRINGS];
    if (!build_score_round_snapshot(records, record_count, native_round,
            expected, &expected_count)) return 0;
    memset(verified, 0, sizeof(verified));
    round_iterate(native_round - 1);
    found_count = 0;
    while (next_couple(&n1, &n2, &value)) {
        found = -1;
        for (i = 0; i < expected_count; i++) {
            if (expected[i].black_papp_id == n1 && expected[i].white_papp_id == n2) {
                found = i;
                break;
            }
        }
        native_score = SCORE_TO_FLOAT(value);
        black_score = (long)native_score;
        if (found < 0 || verified[found] || native_score != (double)black_score ||
            black_score != expected[found].black_score ||
            !SCORES_EQUALITY(OPPONENT_SCORE(value),
                INTEGER_TO_SCORE(expected[found].white_score))) {
            set_tournament_error("native-score-readback-mismatch", "PAPP workfile 原生轮次比分或黑白方向不一致");
            return 0;
        }
        verified[found] = 1;
        found_count++;
    }
    if (found_count != expected_count) {
        set_tournament_error("native-score-readback-missing", "PAPP workfile 缺少一个或多个已登记配对");
        return 0;
    }
    return 1;
}

static int verify_native_workfile_score_rows(const char *filename,
        ScoreBatchRecord *records, long record_count, long expected_round_count,
        int allow_empty_next_round) {
    char *contents;
    char *cursor;
    char *line;
    char *record_line;
    char *line_end;
    ScoreBatchPairing expected[PAPP_SCORE_BATCH_MAX_PAIRINGS];
    long expected_count;
    long native_round;
    long native_round_count;
    long row_count;
    long materialized_round_count;
    long black_papp_id;
    long black_score;
    long white_papp_id;
    long white_score;
    long found;
    long i;
    long length;
    int exists;
    int trailing_empty_round_seen;
    int verified[PAPP_SCORE_BATCH_MAX_PAIRINGS];
    int seen_rounds[NMAX_ROUNDS];
    char trailing;

    contents = read_score_workfile_text(filename, &length, &exists);
    if (contents == NULL || !exists) {
        free(contents);
        if (!exists)
            set_tournament_error("native-workfile-missing", "PAPP workfile 尚无已持久化比分");
        return 0;
    }
    materialized_round_count = score_batch_last_native_round(records, record_count);
    if (materialized_round_count < 1 || materialized_round_count > expected_round_count) {
        set_tournament_error("native-score-round-invalid", "PAPP workfile 的已持久化轮次范围无效");
        free(contents);
        return 0;
    }
    memset(seen_rounds, 0, sizeof(seen_rounds));
    native_round = 0;
    row_count = 0;
    trailing_empty_round_seen = 0;
    cursor = contents;
    while (cursor < contents + length) {
        line = cursor;
        line_end = strchr(cursor, '\n');
        if (line_end != NULL) {
            *line_end = '\0';
            cursor = line_end + 1;
        } else {
            cursor = contents + length;
        }
        if (line[0] != '\0' && line[strlen(line) - 1] == '\r')
            line[strlen(line) - 1] = '\0';
        record_line = line;
        while (*record_line == ' ' || *record_line == '\t') record_line++;
        if (sscanf(record_line, "%% Results of round %ld", &native_round_count) == 1) {
            long max_native_round;
            max_native_round = materialized_round_count;
            if (allow_empty_next_round && materialized_round_count < expected_round_count)
                max_native_round++;
            if (native_round != 0 || native_round_count < 1 ||
                native_round_count > max_native_round ||
                seen_rounds[native_round_count - 1]) {
                set_tournament_error("native-score-round-duplicate", "PAPP workfile 原生轮次标记缺失或重复");
                free(contents);
                return 0;
            }
            if (native_round_count == materialized_round_count + 1) {
                if (!allow_empty_next_round || trailing_empty_round_seen) {
                    set_tournament_error("native-score-round-invalid", "PAPP workfile 含有未持久化的额外原生轮次");
                    free(contents);
                    return 0;
                }
                for (i = 0; i < materialized_round_count; i++) {
                    if (!seen_rounds[i]) {
                        set_tournament_error("native-score-round-missing", "PAPP workfile 在已持久化轮次之前包含额外轮次");
                        free(contents);
                        return 0;
                    }
                }
                trailing_empty_round_seen = 1;
                expected_count = 0;
            } else if (!build_score_round_snapshot(records, record_count, native_round_count,
                    expected, &expected_count)) {
                free(contents);
                return 0;
            }
            native_round = native_round_count;
            seen_rounds[native_round - 1] = 1;
            row_count = 0;
            memset(verified, 0, sizeof(verified));
            continue;
        }
        if (strcmp(record_line, "ronde-suivante;") == 0) {
            if (native_round == 0 || row_count != expected_count) {
                set_tournament_error("native-score-round-incomplete", "PAPP workfile 原生轮次结果缺失或数量不符");
                free(contents);
                return 0;
            }
            native_round = 0;
            continue;
        }
        if (record_line[0] != '(') continue;
        if (native_round == 0 ||
            sscanf(record_line, " ( %ld %ld %ld %ld ) ; %c", &black_papp_id,
                &black_score, &white_papp_id, &white_score, &trailing) != 4 ||
            black_score < 0 || black_score > 64 || white_score < 0 ||
            white_score > 64 || black_score + white_score != 64) {
            set_tournament_error("native-score-row-invalid", "PAPP workfile 中存在无效或游离的原生比分记录");
            free(contents);
            return 0;
        }
        found = -1;
        for (i = 0; i < expected_count; i++) {
            if (expected[i].black_papp_id == black_papp_id &&
                expected[i].white_papp_id == white_papp_id) {
                found = i;
                break;
            }
        }
        if (found < 0 || verified[found] ||
            expected[found].black_score != black_score ||
            expected[found].white_score != white_score) {
            set_tournament_error("native-score-row-mismatch", "PAPP workfile 原生比分存在重复、身份、方向或比分不一致");
            free(contents);
            return 0;
        }
        verified[found] = 1;
        row_count++;
    }
    if (native_round != 0) {
        set_tournament_error("native-score-round-incomplete", "PAPP workfile 原生轮次缺少 ronde-suivante 结束标记");
        free(contents);
        return 0;
    }
    for (i = 0; i < materialized_round_count; i++) {
        if (!seen_rounds[i]) {
            set_tournament_error("native-score-round-missing", "PAPP workfile 缺少原生轮次结果记录");
            free(contents);
            return 0;
        }
    }
    if (allow_empty_next_round && !trailing_empty_round_seen) {
        set_tournament_error("native-score-round-missing", "PAPP workfile 缺少空白的下一原生轮次记录");
        free(contents);
        return 0;
    }
    free(contents);
    return 1;
}

static int read_and_verify_score_workfile(const char *filename,
        ScoreBatchRecord *records, long record_count,
        const char *expected_tournament_name, long expected_round_count,
        int allow_empty_next_round) {
    long result;
    long round;
    long materialized_round_count;
    int verify_empty_next_round;
    const char *saved_filename;
    saved_filename = workfile_filename;
    workfile_filename = (char *)filename;
    use_subfolder = 0;
    first_round();
    result = read_file(workfile_filename, CONFIG_F);
    workfile_filename = (char *)saved_filename;
    if (result != 0) {
        set_tournament_error("native-workfile-invalid", "PAPP 无法重新解析持久化的原生 workfile");
        return 0;
    }
    if (number_of_rounds != expected_round_count ||
        !score_text_equal(tournament_name, expected_tournament_name)) {
        set_tournament_error("native-workfile-context-mismatch", "PAPP workfile 的比赛名称或轮数与当前批次不一致");
        return 0;
    }
    materialized_round_count = score_batch_last_native_round(records, record_count);
    verify_empty_next_round = 0;
    if (materialized_round_count < 1 || materialized_round_count > expected_round_count) {
        set_tournament_error("native-score-round-mismatch", "PAPP workfile 的已推进轮次与持久化比分不一致");
        return 0;
    }
    if (current_round != materialized_round_count) {
        if (!allow_empty_next_round ||
            materialized_round_count >= expected_round_count ||
            current_round != materialized_round_count + 1) {
            set_tournament_error("native-score-round-mismatch", "PAPP workfile 的已推进轮次与持久化比分不一致");
            return 0;
        }
        verify_empty_next_round = 1;
    }
    for (round = 1; round <= expected_round_count; round++)
        if (!verify_score_round_in_papp_core(records, record_count, round)) return 0;
    return verify_native_workfile_score_rows(filename, records,
        record_count, expected_round_count, verify_empty_next_round);
}

static int write_round_registration(FILE *fp, long round_number) {
    long i;
    fprintf(fp, "%% Players inscribed for round %ld\n\n&", round_number);
    for (i = 0; i < tournament_player_count; i++)
        fprintf(fp, " +%06ld", tournament_players[i].papp_id);
    fputs(";\n\n", fp);
    return ferror(fp) == 0;
}

static int replace_score_workfile(const char *temporary, const char *target) {
#if defined(_WIN32)
    if (!MoveFileExA(temporary, target,
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) return 0;
    return 1;
#else
    return rename(temporary, target) == 0;
#endif
}

static int write_score_workfile(const char *target, ScoreBatchRecord *records,
        long record_count, long native_round_count, const char *tournament_title) {
    size_t path_length;
    char *temporary;
    char *saved_filename;
    FILE *fp;
    int descriptor;
    long round;
    long i;
    long materialized_round_count;
    ScoreBatchPairing active[PAPP_SCORE_BATCH_MAX_PAIRINGS];
    long active_count;
    if (record_count < 1) {
        set_tournament_error("invalid-score-batch", "比分批次不能为空");
        return 0;
    }
    materialized_round_count = score_batch_last_native_round(records, record_count);
    if (materialized_round_count < 1 || materialized_round_count > native_round_count) {
        set_tournament_error("native-score-round-invalid", "比分批次的原生轮次范围无效");
        return 0;
    }
    path_length = strlen(target) + 20;
    temporary = (char *)malloc(path_length);
    if (temporary == NULL) {
        set_tournament_error("out-of-memory", "PAPP C 无法创建临时 workfile");
        return 0;
    }
    sprintf(temporary, "%s.tmp.XXXXXX", target);
    descriptor = mkstemp(temporary);
    if (descriptor < 0) {
        free(temporary);
        set_tournament_error("native-workfile-temp-failed", "无法在 PAPP workfile 目录创建临时文件");
        return 0;
    }
#if defined(_WIN32)
    _close(descriptor);
#else
    close(descriptor);
#endif

    saved_filename = workfile_filename;
    workfile_filename = temporary;
    use_subfolder = 0;
    number_of_rounds = native_round_count;
    COPY(tournament_title, &tournament_name);
    save_tournament_infos();
    fp = fopen(temporary, "ab");
    if (fp == NULL) {
        workfile_filename = saved_filename;
        free(temporary);
        set_tournament_error("native-workfile-write-failed", "无法初始化 PAPP 原生 workfile");
        return 0;
    }
    fprintf(fp, "%s\n", PAPP_SCORE_WORKFILE_MARKER);
    if (fclose(fp) != 0) {
        workfile_filename = saved_filename;
        free(temporary);
        set_tournament_error("native-workfile-write-failed", "初始化 PAPP workfile 时发生写入错误");
        return 0;
    }

    first_round();
    for (round = 1; round <= materialized_round_count; round++) {
        current_round = round - 1;
        for (i = 0; i < tournament_player_count; i++) present[i] = 1;
        if (!build_score_round_snapshot(records, record_count, round,
                active, &active_count)) {
            workfile_filename = saved_filename;
            free(temporary);
            return 0;
        }
        fp = fopen(temporary, "ab");
        if (fp == NULL || !write_round_registration(fp, round)) {
            if (fp != NULL) fclose(fp);
            workfile_filename = saved_filename;
            free(temporary);
            set_tournament_error("native-workfile-write-failed", "无法写入 PAPP 原生轮次签到记录");
            return 0;
        }
        if (fclose(fp) != 0) {
            workfile_filename = saved_filename;
            free(temporary);
            set_tournament_error("native-workfile-write-failed", "写入 PAPP 轮次签到记录时发生错误");
            return 0;
        }
        zero_coupling();
        for (i = 0; i < active_count; i++)
            make_couple(active[i].black_papp_id, active[i].white_papp_id,
                INTEGER_TO_SCORE(active[i].black_score));
        save_round();
        if (round < native_round_count) next_round();
    }
    fp = fopen(temporary, "ab");
    if (fp == NULL) {
        workfile_filename = saved_filename;
        free(temporary);
        set_tournament_error("native-workfile-write-failed", "无法写入 PAPP 批次标记");
        return 0;
    }
    for (i = 0; i < record_count; i++) {
        if (records[i].raw_json != NULL) {
            fputs(PAPP_SCORE_BATCH_MARKER, fp);
            fputs(records[i].raw_json, fp);
            fputc('\n', fp);
        } else if (!write_score_batch_metadata(fp, &records[i])) {
            fclose(fp);
            workfile_filename = saved_filename;
            free(temporary);
            set_tournament_error("native-workfile-write-failed", "序列化 PAPP 批次元数据失败");
            return 0;
        }
    }
    if (fclose(fp) != 0) {
        workfile_filename = saved_filename;
        free(temporary);
        set_tournament_error("native-workfile-write-failed", "写入 PAPP 批次标记时发生错误");
        return 0;
    }
    if (!read_and_verify_score_workfile(temporary, records, record_count,
            tournament_title, native_round_count, 0)) {
        workfile_filename = saved_filename;
        free(temporary);
        return 0;
    }
    if (!replace_score_workfile(temporary, target)) {
        workfile_filename = saved_filename;
        free(temporary);
        set_tournament_error("native-workfile-replace-failed", "无法原子更新 PAPP workfile");
        return 0;
    }
    workfile_filename = (char *)target;
    if (!read_and_verify_score_workfile(target, records, record_count,
            tournament_title, native_round_count, 0)) {
        workfile_filename = saved_filename;
        free(temporary);
        return 0;
    }
    workfile_filename = saved_filename;
    free(temporary);
    return 1;
}

static int score_batch_expected_matches(const ScoreBatchRecord *stored,
        const ScoreBatchRecord *expected, const JsonValue *pairing_ids) {
    JsonValue *id_json;
    const char *id;
    ScoreBatchPairing *actual;
    long i;
    long j;
    if (!score_text_equal(stored->batch_id, expected->batch_id) ||
        !score_text_equal(stored->stage, expected->stage) ||
        stored->round != expected->round ||
        stored->pairing_count != expected->pairing_count ||
        pairing_ids == NULL || pairing_ids->type != JSON_ARRAY ||
        pairing_ids->item_count != expected->pairing_count) return 0;
    for (i = 0; i < pairing_ids->item_count; i++) {
        id_json = json_at(pairing_ids, i);
        id = json_identity(id_json);
        if (id == NULL || score_batch_find_pairing((ScoreBatchRecord *)expected, id) == NULL)
            return 0;
        if (score_batch_find_pairing((ScoreBatchRecord *)stored, id) == NULL) return 0;
        for (j = 0; j < i; j++)
            if (score_text_equal(id, json_identity(json_at(pairing_ids, j)))) return 0;
    }
    for (i = 0; i < expected->pairing_count; i++) {
        actual = score_batch_find_pairing((ScoreBatchRecord *)stored,
            expected->pairings[i].id);
        if (actual == NULL || actual->table != expected->pairings[i].table ||
            !score_pairing_identity_equal(actual, &expected->pairings[i]) ||
            !score_text_equal(actual->oq_game_id, expected->pairings[i].oq_game_id) ||
            actual->black_score != expected->pairings[i].black_score ||
            actual->white_score != expected->pairings[i].white_score) return 0;
    }
    return 1;
}

static int score_batch_matches_current_native_round(const ScoreBatchRecord *stored,
        long record_count, const ScoreBatchRecord *expected) {
    ScoreBatchPairing active[PAPP_SCORE_BATCH_MAX_PAIRINGS];
    ScoreBatchPairing *current;
    long active_count;
    long i;
    long j;
    if (!build_score_round_snapshot((ScoreBatchRecord *)stored, record_count,
            expected->native_round, active, &active_count)) return 0;
    for (i = 0; i < expected->pairing_count; i++) {
        current = NULL;
        for (j = 0; j < active_count; j++) {
            if (strcmp(active[j].id, expected->pairings[i].id) == 0) {
                current = &active[j];
                break;
            }
        }
        if (current == NULL || current->table != expected->pairings[i].table ||
            !score_pairing_identity_equal(current, &expected->pairings[i]) ||
            !score_text_equal(current->oq_game_id, expected->pairings[i].oq_game_id) ||
            current->black_score != expected->pairings[i].black_score ||
            current->white_score != expected->pairings[i].white_score) return 0;
    }
    return 1;
}

static int operation_score_batch(const JsonValue *root, int readback) {
    JsonValue *batch_ids;
    ScoreBatchRecord incoming;
    ScoreBatchRecord *records;
    ScoreBatchRecord *existing_batch;
    ScoreBatchRecord *resized;
    char *contents;
    const char *environment_path;
    const char *path;
    const char *tournament_title;
    long record_count;
    int allow_empty_next_round;
    long i;
    int exists;

    environment_path = getenv("PAPP_TOURNAMENT_WORKFILE");
    path = environment_path != NULL && *environment_path != '\0'
        ? environment_path : workfile_filename;
    workfile_filename = (char *)path;
    use_subfolder = 0;
    if (!configure_core(root)) return 0;
    if (!score_batch_record_from_json((JsonValue *)root, &incoming, 0)) return 0;
    tournament_title = incoming.tournament_name;
    if (tournament_title == NULL || *tournament_title == '\0') tournament_title = "PAPP Tournament";
    incoming.tournament_name = tournament_title;

    if (!load_score_workfile(path, &records, &record_count, &contents, &exists)) return 0;
    if (readback && !exists) {
        set_tournament_error("native-workfile-missing", "PAPP workfile 尚无已持久化比分");
        return 0;
    }
    if (exists) {
        for (i = 0; i < record_count; i++) {
            if (!score_batch_context_matches(&records[i], &incoming) ||
                !score_batch_player_map_matches(&records[i])) {
                set_tournament_error("native-workfile-context-mismatch", "当前选手名单或比赛参数与 PAPP workfile 不一致");
                return 0;
            }
        }
        allow_empty_next_round = !readback && incoming.native_round ==
            score_batch_last_native_round(records, record_count) + 1;
        if (!read_and_verify_score_workfile(path, records, record_count,
                tournament_title, incoming.native_round_count,
                allow_empty_next_round)) return 0;
    }

    if (readback) {
        batch_ids = json_get(root, "pairingIds");
        existing_batch = score_batch_find_record(records, record_count, incoming.batch_id);
        if (existing_batch == NULL ||
            !score_batch_expected_matches(existing_batch, &incoming, batch_ids) ||
            !score_batch_matches_current_native_round(records, record_count, &incoming)) {
            set_tournament_error("score-readback-mismatch", "PAPP workfile 缺少此批次，或配对身份、方向、桌号及比分不一致");
            return 0;
        }
        if (incoming.native_round > NMAX_ROUNDS ||
            !verify_score_round_in_papp_core(records, record_count, incoming.native_round)) return 0;
        printf("{\"ok\":true,\"source\":\"papp-c\",\"operation\":\"read-score-batch\",\"verified\":true,\"batchId\":");
        write_json_string(incoming.batch_id);
        printf(",\"pairings\":[");
        for (i = 0; i < existing_batch->pairing_count; i++) {
            const ScoreBatchPairing *pairing = &existing_batch->pairings[i];
            if (i) putchar(',');
            printf("{\"id\":"); write_json_string(pairing->id);
            printf(",\"table\":%ld,\"blackId\":", pairing->table);
            write_json_string(pairing->black_id);
            printf(",\"whiteId\":"); write_json_string(pairing->white_id);
            printf(",\"black\":"); write_json_string(pairing->black);
            printf(",\"white\":"); write_json_string(pairing->white);
            printf(",\"blackAccount\":"); write_json_string(pairing->black_account);
            printf(",\"whiteAccount\":"); write_json_string(pairing->white_account);
            printf(",\"status\":\"completed\",\"blackScore\":%ld,\"whiteScore\":%ld}",
                pairing->black_score, pairing->white_score);
        }
        printf("]}\n");
        return 1;
    }

    existing_batch = score_batch_find_record(records, record_count, incoming.batch_id);
    if (existing_batch != NULL) {
        if (!score_batch_records_equal(existing_batch, &incoming)) {
            set_tournament_error("score-batch-id-conflict", "同一 batchId 已用于不同身份或比分，PAPP 未覆盖原记录");
            return 0;
        }
        printf("{\"ok\":true,\"source\":\"papp-c\",\"operation\":\"write-score-batch\",\"accepted\":true,\"idempotent\":true,\"batchId\":");
        write_json_string(incoming.batch_id);
        printf("}\n");
        return 1;
    }
    resized = (ScoreBatchRecord *)realloc(records,
        (size_t)(record_count + 1) * sizeof(ScoreBatchRecord));
    if (resized == NULL) {
        set_tournament_error("out-of-memory", "PAPP C 无法登记比分批次");
        return 0;
    }
    records = resized;
    records[record_count++] = incoming;
    for (i = 1; i <= incoming.native_round_count; i++) {
        ScoreBatchPairing active[PAPP_SCORE_BATCH_MAX_PAIRINGS];
        long active_count;
        if (!build_score_round_snapshot(records, record_count, i, active, &active_count))
            return 0;
    }
    if (!write_score_workfile(path, records, record_count,
            incoming.native_round_count, tournament_title)) return 0;
    printf("{\"ok\":true,\"source\":\"papp-c\",\"operation\":\"write-score-batch\",\"accepted\":true,\"idempotent\":false,\"batchId\":");
    write_json_string(incoming.batch_id);
    printf("}\n");
    (void)contents;
    return 1;
}

typedef struct {
    long black_score;
    long white_score;
    long black_discs;
    long white_discs;
    long empty_squares;
    long played_moves;
    long explicit_passes;
    long start_position_moves;
    long terminal_move_index;
    char terminal_status[96];
    char side_to_move[8];
    char cause[32];
    char error[256];
} OqReplay;

static const char *trim_text(const char *value, char *buffer, size_t capacity) {
    size_t start;
    size_t end;
    size_t length;
    if (value == NULL || capacity == 0) return NULL;
    start = 0;
    end = strlen(value);
    while (start < end && isspace((unsigned char)value[start])) start++;
    while (end > start && isspace((unsigned char)value[end - 1])) end--;
    length = end - start;
    if (length >= capacity) length = capacity - 1;
    memcpy(buffer, value + start, length);
    buffer[length] = '\0';
    return buffer;
}

static const char *first_json_text(const JsonValue *object, const char *a,
        const char *b, const char *c, const char *d) {
    JsonValue *value;
    value = json_get(object, a);
    if (value == NULL || value->type == JSON_NULL || json_text(value) == NULL ||
        *json_text(value) == '\0') value = json_get(object, b);
    if ((value == NULL || value->type == JSON_NULL || json_text(value) == NULL ||
         *json_text(value) == '\0') && c != NULL) value = json_get(object, c);
    if ((value == NULL || value->type == JSON_NULL || json_text(value) == NULL ||
         *json_text(value) == '\0') && d != NULL) value = json_get(object, d);
    return json_text(value);
}

static const char *entry_account(const JsonValue *entry, int black) {
    JsonValue *players_json;
    JsonValue *player_json;
    JsonValue *value;
    const char *account;
    if (black)
        account = first_json_text(entry, "black_name", "blackName", "blackAccount", NULL);
    else
        account = first_json_text(entry, "white_name", "whiteName", "whiteAccount", NULL);
    if (account != NULL && *account != '\0') return account;
    players_json = json_get(entry, "players");
    player_json = json_at(players_json, black ? 0 : 1);
    value = json_get_any(player_json, "id", "account", "name");
    return json_text(value);
}

static const char *entry_game_id(const JsonValue *entry) {
    return first_json_text(entry, "game_id", "gameId", "id", NULL);
}

static const char *entry_created_at(const JsonValue *entry) {
    const char *value;
    JsonValue *detail;
    value = first_json_text(entry, "created_at", "createdAt", "created", "resultTime");
    if (value != NULL) return value;
    detail = json_get_any(entry, "detail", "raw_detail", "rawDetail");
    if (detail == NULL) detail = json_get_any(entry, "game_detail", "gameDetail", NULL);
    return first_json_text(detail, "created", "createdAt", NULL, NULL);
}

static int account_key(const char *value, char *output, size_t capacity) {
    size_t used;
    const unsigned char *cursor;
    unsigned char character;
    unsigned long codepoint;
    char trimmed[512];
    if (trim_text(value, trimmed, sizeof(trimmed)) == NULL || capacity == 0) return 0;
    used = 0;
    for (cursor = (const unsigned char *)trimmed; *cursor && used + 1 < capacity;) {
        character = *cursor;
        if ((character >= 'a' && character <= 'z') ||
            (character >= 'A' && character <= 'Z') ||
            (character >= '0' && character <= '9')) {
            output[used++] = (char)tolower(character);
            cursor++;
        } else if (character >= 0xe0 && character <= 0xef && cursor[1] != '\0' &&
            cursor[2] != '\0' && (cursor[1] & 0xc0) == 0x80 &&
            (cursor[2] & 0xc0) == 0x80) {
            codepoint = ((unsigned long)(character & 0x0f) << 12) |
                ((unsigned long)(cursor[1] & 0x3f) << 6) |
                (unsigned long)(cursor[2] & 0x3f);
            if (codepoint >= 0x4e00 && codepoint <= 0x9fff && used + 3 < capacity) {
                output[used++] = (char)cursor[0];
                output[used++] = (char)cursor[1];
                output[used++] = (char)cursor[2];
            }
            cursor += 3;
        } else {
            cursor++;
        }
    }
    output[used] = '\0';
    return used > 0;
}

static long days_from_civil(long year, long month, long day) {
    long era;
    long year_of_era;
    long day_of_year;
    long day_of_era;
    year -= month <= 2;
    era = (year >= 0 ? year : year - 399) / 400;
    year_of_era = year - era * 400;
    day_of_year = (153 * (month + (month > 2 ? -3 : 9)) + 2) / 5 + day - 1;
    day_of_era = year_of_era * 365 + year_of_era / 4 - year_of_era / 100 + day_of_year;
    return era * 146097 + day_of_era - 719468;
}

static int parse_date_millis(const JsonValue *value, double *millis) {
    const char *text;
    char *end;
    long year;
    long month;
    long day;
    long hour;
    long minute;
    long second;
    long offset_minutes;
    long fraction_millis;
    long day_count;
    size_t i;
    size_t length;
    double parsed;
    int fields;
    char timezone_sign;
    text = json_text(value);
    if (text == NULL) return 0;
    if (value->type == JSON_NUMBER) {
        errno = 0;
        parsed = strtod(text, &end);
        if (errno == ERANGE || *end != '\0') return 0;
        *millis = parsed > 10000000000.0 ? parsed : parsed * 1000.0;
        return 1;
    }
    year = month = day = hour = minute = second = 0;
    fields = sscanf(text, "%4ld-%2ld-%2ldT%2ld:%2ld:%2ld", &year, &month, &day,
        &hour, &minute, &second);
    /* A space separator leaves the T scan at three date fields. */
    if (fields == 3 && strlen(text) > 10 && text[10] == ' ') {
        fields = sscanf(text, "%4ld-%2ld-%2ld %2ld:%2ld:%2ld", &year, &month, &day,
            &hour, &minute, &second);
    }
    if (fields < 3 || month < 1 || month > 12 || day < 1 || day > 31 ||
        hour > 23 || minute > 59 || second > 60)
        return 0;
    if (fields == 3) hour = minute = second = 0;
    length = strlen(text);
    i = 10;
    while (i < length && text[i] != 'Z' && text[i] != 'z' && text[i] != '+' && text[i] != '-') i++;
    offset_minutes = 8 * 60;
    if (i < length && (text[i] == 'Z' || text[i] == 'z')) offset_minutes = 0;
    else if (i < length && (text[i] == '+' || text[i] == '-')) {
        timezone_sign = text[i];
        if (i + 4 < length && isdigit((unsigned char)text[i + 1]) &&
            isdigit((unsigned char)text[i + 2]) &&
            isdigit((unsigned char)text[i + 3]) &&
            isdigit((unsigned char)text[i + 4]) && text[i + 3] != ':') {
            offset_minutes = (text[i + 1] - '0') * 600 + (text[i + 2] - '0') * 60 +
                (text[i + 3] - '0') * 10 + (text[i + 4] - '0');
        } else if (i + 5 < length && isdigit((unsigned char)text[i + 1]) &&
            isdigit((unsigned char)text[i + 2]) && text[i + 3] == ':' &&
            isdigit((unsigned char)text[i + 4]) && isdigit((unsigned char)text[i + 5])) {
            offset_minutes = (text[i + 1] - '0') * 600 + (text[i + 2] - '0') * 60 +
                (text[i + 4] - '0') * 10 + (text[i + 5] - '0');
        } else return 0;
        if (offset_minutes > 14 * 60) return 0;
        if (timezone_sign == '-') offset_minutes = -offset_minutes;
    }
    fraction_millis = 0;
    for (i = 19; i < length && text[i] != 'Z' && text[i] != 'z' &&
        text[i] != '+' && text[i] != '-'; i++) {
        if (text[i] == '.') {
            long factor;
            factor = 100;
            i++;
            while (i < length && isdigit((unsigned char)text[i]) && factor > 0) {
                fraction_millis += (text[i] - '0') * factor;
                factor /= 10;
                i++;
            }
            break;
        }
    }
    day_count = days_from_civil(year, month, day);
    *millis = (double)day_count * 86400000.0 + (double)hour * 3600000.0 +
        (double)minute * 60000.0 + (double)second * 1000.0 +
        (double)fraction_millis - (double)offset_minutes * 60000.0;
    return 1;
}

static const JsonValue *entry_detail(const JsonValue *entry, int required) {
    JsonValue *detail;
    JsonValue *metadata;
    JsonValue *position;
    JsonValue *metadata_value;
    const char *metadata_text;
    const char *error;
    detail = json_get_any(entry, "detail", "raw_detail", "rawDetail");
    if (detail == NULL) detail = json_get_any(entry, "game_detail", "gameDetail", NULL);
    position = json_get(detail, "position");
    if (position != NULL) return detail;
    metadata_value = json_get_any(entry, "raw_metadata_json", "rawMetadataJson", "rawMetadata");
    if (metadata_value == NULL) metadata_value = json_get(entry, "metadata");
    metadata = metadata_value;
    metadata_text = json_text(metadata_value);
    if (metadata_value != NULL && metadata_value->type == JSON_STRING && metadata_text != NULL) {
        metadata = parse_json_document(metadata_text, strlen(metadata_text), &error);
        if (metadata == NULL && required) {
            strcpy(tournament_error, "invalid-oq-metadata|OQ detail metadata is not valid JSON");
            return NULL;
        }
    }
    if (metadata != NULL) {
        detail = json_get(metadata, "detail");
        if (json_get(detail, "position") != NULL) return detail;
        detail = json_get(metadata, "oqDetail");
        if (json_get(detail, "position") != NULL) return detail;
        detail = json_get(metadata, "summary");
        if (json_get(detail, "position") != NULL) return detail;
        detail = json_get(metadata, "gameRecord");
        if (json_get(detail, "position") != NULL) return detail;
    }
    position = json_get(entry, "position");
    if (position != NULL && (json_get(position, "moves") != NULL ||
        json_get(position, "startPos") != NULL)) return entry;
    if (required) strcpy(tournament_error,
        "missing-oq-detail|OQ game detail with position.moves is required for automatic SCORE scoring");
    return NULL;
}

static int coordinate(const char *value, long *row, long *column) {
    char first;
    if (value == NULL || strlen(value) != 2) return 0;
    first = (char)tolower((unsigned char)value[0]);
    if (first < 'a' || first > 'h' || value[1] < '1' || value[1] > '8') return 0;
    *row = value[1] - '1';
    *column = first - 'a';
    return 1;
}

static long legal_flips(int board[8][8], long row, long column, int color,
        long flip_rows[64], long flip_columns[64]) {
    static const long directions[8][2] = {
        {-1, -1}, {-1, 0}, {-1, 1}, {0, -1},
        {0, 1}, {1, -1}, {1, 0}, {1, 1}
    };
    long direction;
    long r;
    long c;
    long count;
    long captured_rows[8];
    long captured_columns[8];
    long captured;
    long i;
    int other;
    if (row < 0 || row > 7 || column < 0 || column > 7 || board[row][column] != 0)
        return 0;
    other = color == 1 ? 2 : 1;
    count = 0;
    for (direction = 0; direction < 8; direction++) {
        r = row + directions[direction][0];
        c = column + directions[direction][1];
        captured = 0;
        while (r >= 0 && r < 8 && c >= 0 && c < 8 && board[r][c] == other && captured < 8) {
            captured_rows[captured] = r;
            captured_columns[captured] = c;
            captured++;
            r += directions[direction][0];
            c += directions[direction][1];
        }
        if (captured > 0 && r >= 0 && r < 8 && c >= 0 && c < 8 && board[r][c] == color) {
            for (i = 0; i < captured && count < 64; i++) {
                flip_rows[count] = captured_rows[i];
                flip_columns[count] = captured_columns[i];
                count++;
            }
        }
    }
    return count;
}

static int has_legal_move(int board[8][8], int color) {
    long row;
    long column;
    long flip_rows[64];
    long flip_columns[64];
    for (row = 0; row < 8; row++)
        for (column = 0; column < 8; column++)
            if (legal_flips(board, row, column, color, flip_rows, flip_columns) > 0)
                return 1;
    return 0;
}

static int apply_oq_move(int board[8][8], int color, const char *move,
        long move_index, OqReplay *replay) {
    long row;
    long column;
    long flip_rows[64];
    long flip_columns[64];
    long flip_count;
    long i;
    char coordinate_buffer[16];
    if (!coordinate(move, &row, &column)) {
        sprintf(replay->error, "bad OQ move coordinate: %s", move == NULL ? "" : move);
        return 0;
    }
    flip_count = legal_flips(board, row, column, color, flip_rows, flip_columns);
    if (flip_count == 0) {
        sprintf(coordinate_buffer, "%s", move);
        sprintf(replay->error, "illegal OQ move at ply %ld: %s", move_index, coordinate_buffer);
        return 0;
    }
    board[row][column] = color;
    for (i = 0; i < flip_count; i++) board[flip_rows[i]][flip_columns[i]] = color;
    replay->played_moves++;
    return 1;
}

static void count_oq_board(int board[8][8], OqReplay *replay) {
    long row;
    long column;
    replay->black_discs = 0;
    replay->white_discs = 0;
    for (row = 0; row < 8; row++)
        for (column = 0; column < 8; column++) {
            if (board[row][column] == 1) replay->black_discs++;
            else if (board[row][column] == 2) replay->white_discs++;
        }
    replay->empty_squares = 64 - replay->black_discs - replay->white_discs;
}

static int replay_oq_entry(const JsonValue *entry, OqReplay *replay, int terminal_only) {
    static const long start_row[4] = {3, 4, 3, 4};
    static const long start_column[4] = {3, 4, 4, 3};
    static const int start_color[4] = {2, 2, 1, 1};
    int board[8][8];
    const JsonValue *detail;
    JsonValue *position;
    JsonValue *moves;
    JsonValue *move_json;
    JsonValue *status_json;
    JsonValue *move_json_text;
    const char *start_pos;
    const char *move_text;
    const char *status_text;
    long row;
    long column;
    long index;
    long i;
    int color;
    int status_result;
    size_t cursor;
    char coord_text[3];

    memset(replay, 0, sizeof(*replay));
    for (row = 0; row < 8; row++) for (column = 0; column < 8; column++) board[row][column] = 0;
    for (i = 0; i < 4; i++) board[start_row[i]][start_column[i]] = start_color[i];
    detail = entry_detail(entry, 1);
    if (detail == NULL) return 0;
    position = json_get(detail, "position");
    moves = json_get(position, "moves");
    if (moves == NULL || moves->type != JSON_ARRAY) {
        strcpy(replay->error, "OQ detail position.moves is not a list");
        return 0;
    }
    start_pos = json_text(json_get(position, "startPos"));
    color = 1;
    index = 0;
    if (start_pos != NULL) {
        cursor = 0;
        while (start_pos[cursor] != '\0') {
            if (tolower((unsigned char)start_pos[cursor]) >= 'a' &&
                tolower((unsigned char)start_pos[cursor]) <= 'h' &&
                start_pos[cursor + 1] >= '1' && start_pos[cursor + 1] <= '8') {
                coord_text[0] = start_pos[cursor];
                coord_text[1] = start_pos[cursor + 1];
                coord_text[2] = '\0';
                if (!apply_oq_move(board, color, coord_text, ++index, replay)) return 0;
                color = color == 1 ? 2 : 1;
                replay->start_position_moves++;
                cursor += 2;
            } else cursor++;
        }
    }
    for (i = 0; i < moves->item_count; i++) {
        move_json = json_at(moves, i);
        move_json_text = json_get(move_json, "m");
        move_text = json_text(move_json_text);
        status_json = json_get(move_json, "s");
        status_text = json_text(status_json);
        status_result = 0;
        if (status_text != NULL) {
            if (strstr(status_text, "WIN:") != NULL || strstr(status_text, "win:") != NULL)
                status_result = 1;
            else if (strstr(status_text, "LOSE:") != NULL || strstr(status_text, "lose:") != NULL)
                status_result = -1;
            if (status_result != 0 && terminal_only) {
                const char *cause_start;
                long loser;
                cause_start = strchr(status_text, ':');
                cause_start = cause_start == NULL ? "" : cause_start + 1;
                while (*cause_start && isspace((unsigned char)*cause_start)) cause_start++;
                for (row = 0; row < 31 && cause_start[row] != '\0'; row++)
                    replay->cause[row] = (char)tolower((unsigned char)cause_start[row]);
                replay->cause[row] = '\0';
                if (strcmp(replay->cause, "resign") != 0 && strcmp(replay->cause, "timeup") != 0 &&
                    strcmp(replay->cause, "timeout") != 0 && strcmp(replay->cause, "disconnect") != 0 &&
                    strcmp(replay->cause, "disconnected") != 0)
                    continue;
                if (strcmp(replay->cause, "timeup") == 0) strcpy(replay->cause, "timeout");
                loser = status_result < 0 ? color : (color == 1 ? 2 : 1);
                replay->black_score = loser == 1 ? 0 : 64;
                replay->white_score = loser == 2 ? 0 : 64;
                replay->terminal_move_index = i + 1;
                strncpy(replay->terminal_status, status_text, sizeof(replay->terminal_status) - 1);
                replay->terminal_status[sizeof(replay->terminal_status) - 1] = '\0';
                strcpy(replay->side_to_move, color == 1 ? "black" : "white");
                return 1;
            }
        }
        if (move_text != NULL && coordinate(move_text, &row, &column)) {
            if (!apply_oq_move(board, color, move_text, ++index, replay)) return 0;
            color = color == 1 ? 2 : 1;
        } else if (move_text != NULL && strcmp(move_text, "-") == 0) {
            if (has_legal_move(board, color)) {
                sprintf(replay->error, "OQ explicit pass at move %ld while %s has legal moves",
                    i + 1, color == 1 ? "black" : "white");
                return 0;
            }
            replay->explicit_passes++;
            color = color == 1 ? 2 : 1;
        }
    }
    if (terminal_only) {
        strcpy(replay->error, "OQ terminal resign/timeout/disconnect status not found in position.moves");
        return 0;
    }
    count_oq_board(board, replay);
    if (replay->black_discs > replay->white_discs) {
        replay->black_score = replay->black_discs + replay->empty_squares;
        replay->white_score = replay->white_discs;
    } else if (replay->white_discs > replay->black_discs) {
        replay->black_score = replay->black_discs;
        replay->white_score = replay->white_discs + replay->empty_squares;
    } else if (replay->empty_squares % 2) {
        sprintf(replay->error, "OQ replay ended tied with odd empty count: %ld-%ld, empty %ld",
            replay->black_discs, replay->white_discs, replay->empty_squares);
        return 0;
    } else {
        replay->black_score = replay->black_discs + replay->empty_squares / 2;
        replay->white_score = replay->white_discs + replay->empty_squares / 2;
    }
    return 1;
}

static int score_oq_entry(const JsonValue *entry, OqReplay *replay) {
    const char *status;
    const char *comment;
    const char *terminal;
    char cause[32];
    long loser;
    JsonValue *comment_json;
    status = first_json_text(entry, "finalStatus", "final_status", "status", NULL);
    comment_json = json_get_any(entry, "comment", "notes", NULL);
    comment = json_text(comment_json);
    cause[0] = '\0';
    if (status != NULL) {
        if (strstr(status, "resign") != NULL || strstr(status, "RESIGN") != NULL) strcpy(cause, "resign");
        else if (strstr(status, "timeup") != NULL || strstr(status, "TIMEUP") != NULL) strcpy(cause, "timeout");
        else if (strstr(status, "timeout") != NULL || strstr(status, "TIMEOUT") != NULL) strcpy(cause, "timeout");
        else if (strstr(status, "disconnect") != NULL || strstr(status, "DISCONNECT") != NULL) strcpy(cause, "disconnect");
        else if (strstr(status, "disconnected") != NULL || strstr(status, "DISCONNECTED") != NULL) strcpy(cause, "disconnected");
        else if (strchr(status, ':') != NULL) {
            terminal = strchr(status, ':') + 1;
            while (*terminal && isspace((unsigned char)*terminal)) terminal++;
            if (equal_case_prefix(terminal, "resign", 6)) strcpy(cause, "resign");
            else if (equal_case_prefix(terminal, "timeup", 6) || equal_case_prefix(terminal, "timeout", 7)) strcpy(cause, "timeout");
            else if (equal_case_prefix(terminal, "disconnect", 10)) strcpy(cause, "disconnect");
        }
    }
    if (cause[0] == '\0' && comment != NULL) {
        terminal = strstr(comment, "terminal_status");
        if (terminal != NULL) {
            terminal = strchr(terminal, '=');
            if (terminal != NULL) {
                terminal++;
                while (*terminal && isspace((unsigned char)*terminal)) terminal++;
                if (equal_case_prefix(terminal, "WIN:RESIGN", 10) || equal_case_prefix(terminal, "LOSE:RESIGN", 11)) strcpy(cause, "resign");
                else if (equal_case_prefix(terminal, "WIN:TIMEUP", 10) || equal_case_prefix(terminal, "LOSE:TIMEUP", 11)) strcpy(cause, "timeout");
                else if (equal_case_prefix(terminal, "WIN:TIMEOUT", 11) || equal_case_prefix(terminal, "LOSE:TIMEOUT", 12)) strcpy(cause, "timeout");
                else if (equal_case_prefix(terminal, "WIN:DISCONNECT", 14) || equal_case_prefix(terminal, "LOSE:DISCONNECT", 15)) strcpy(cause, "disconnect");
            }
        }
    }
    if (cause[0] != '\0') {
        if (!replay_oq_entry(entry, replay, 1)) return 0;
        if (replay->black_score == 0) loser = 1;
        else loser = 2;
        replay->black_discs = -loser;
        return 1;
    }
    return replay_oq_entry(entry, replay, 0);
}

#define OQ_MAX_CANDIDATES 1024
#define OQ_MAX_PENDING_CANDIDATES 8

typedef struct {
    const JsonValue *entry;
    char key[512];
    double created_millis;
    long seen_mask;
    long black_score;
    long white_score;
    int score_valid;
    OqReplay replay;
    char score_reason[256];
} OqCandidate;

static const char *pairing_text(const JsonValue *pairing, const char *first,
        const char *second) {
    JsonValue *value;
    value = json_get(pairing, first);
    if (value == NULL || value->type == JSON_NULL || json_text(value) == NULL ||
        *json_text(value) == '\0') value = json_get(pairing, second);
    return json_text(value);
}

static const char *pairing_table(const JsonValue *pairing, char *buffer, size_t capacity) {
    const char *value;
    value = json_text(json_get(pairing, "table"));
    if (value == NULL) value = json_text(json_get(pairing, "pendingTable"));
    if (value == NULL) value = "";
    if (capacity == 0) return value;
    strncpy(buffer, value, capacity - 1);
    buffer[capacity - 1] = '\0';
    return buffer;
}

static int text_equals(const char *left, const char *right) {
    if (left == NULL || right == NULL) return left == right;
    return strcmp(left, right) == 0;
}

static int contains_text_key(const JsonValue *value, const char *candidate_key,
        const char *game_id) {
    const char *text;
    if (value == NULL || (text = json_text(value)) == NULL || *text == '\0') return 0;
    if (candidate_key != NULL && strcmp(text, candidate_key) == 0) return 1;
    if (game_id != NULL && *game_id != '\0' && strcmp(text, game_id) == 0) return 1;
    if (candidate_key != NULL && strncmp(text, "oq-auto:", 8) == 0 &&
        strcmp(text + 8, candidate_key) == 0) return 1;
    if (game_id != NULL && *game_id != '\0' && strncmp(text, "id:", 3) == 0 &&
        strcmp(text + 3, game_id) == 0) return 1;
    if (candidate_key != NULL && strncmp(candidate_key, "id:", 3) == 0 &&
        strncmp(text, "oq-auto:", 8) == 0 && strcmp(text + 8, candidate_key) == 0)
        return 1;
    return 0;
}

static void make_candidate_key(const JsonValue *entry, char *key, size_t capacity) {
    const char *game_id;
    const char *created;
    const char *black_score;
    const char *white_score;
    const char *status;
    char black_key[256];
    char white_key[256];
    char raw[512];
    size_t used;
    size_t length;
    game_id = entry_game_id(entry);
    if (game_id != NULL && *game_id != '\0') {
        strncpy(key, "id:", capacity - 1);
        key[capacity - 1] = '\0';
        used = strlen(key);
        length = strlen(game_id);
        if (length > capacity - used - 1) length = capacity - used - 1;
        memcpy(key + used, game_id, length);
        key[used + length] = '\0';
        return;
    }
    created = entry_created_at(entry);
    black_score = first_json_text(entry, "black_score", "blackScore", NULL, NULL);
    white_score = first_json_text(entry, "white_score", "whiteScore", NULL, NULL);
    status = first_json_text(entry, "status", "finalStatus", "final_status", NULL);
    account_key(entry_account(entry, 1), black_key, sizeof(black_key));
    account_key(entry_account(entry, 0), white_key, sizeof(white_key));
    sprintf(raw, "fallback:%s:%s:%s:%s:%s:%s",
        created == NULL ? "" : created,
        black_key, white_key,
        black_score == NULL ? "" : black_score,
        white_score == NULL ? "" : white_score,
        status == NULL ? "" : status);
    strncpy(key, raw, capacity - 1);
    key[capacity - 1] = '\0';
}

static int collect_oq_candidates(const JsonValue *games_json, const char *black_account,
        const char *white_account, double start_time, double end_time,
        OqCandidate *candidates, long *candidate_count) {
    char black_key[256];
    char white_key[256];
    char listed_key[256];
    char actual_black[256];
    char actual_white[256];
    JsonValue *raw_games;
    JsonValue *games_array;
    JsonValue *entry;
    const char *key_text;
    double created_time;
    long i;
    long j;
    long found;
    long bit;
    long count;
    long candidate_index;
    if (games_json == NULL || games_json->type != JSON_OBJECT) {
        *candidate_count = 0;
        return 1;
    }
    if (!account_key(black_account, black_key, sizeof(black_key)) ||
        !account_key(white_account, white_key, sizeof(white_key)) ||
        strcmp(black_key, white_key) == 0) {
        *candidate_count = 0;
        return 1;
    }
    count = 0;
    for (i = 0; i < games_json->member_count; i++) {
        key_text = games_json->members[i].key;
        if (!account_key(key_text, listed_key, sizeof(listed_key))) continue;
        bit = strcmp(listed_key, black_key) == 0 ? 1L :
            strcmp(listed_key, white_key) == 0 ? 2L : 0L;
        if (bit == 0) continue;
        raw_games = games_json->members[i].value;
        games_array = raw_games;
        if (raw_games != NULL && raw_games->type == JSON_OBJECT)
            games_array = json_get(raw_games, "games");
        if (games_array == NULL || games_array->type != JSON_ARRAY) continue;
        for (j = 0; j < games_array->item_count; j++) {
            entry = json_at(games_array, j);
            if (entry == NULL || entry->type != JSON_OBJECT ||
                !parse_date_millis(json_get(entry, "created_at"), &created_time)) {
                if (entry != NULL && entry->type == JSON_OBJECT) {
                    JsonValue *created_json;
                    created_json = json_get_any(entry, "created_at", "createdAt", "created");
                    if (!parse_date_millis(created_json, &created_time)) {
                        created_json = json_get_any(entry, "resultTime", "createdAt", NULL);
                        if (!parse_date_millis(created_json, &created_time) &&
                            !parse_date_millis(json_get(entry, "detail") == NULL ? NULL :
                                json_get(json_get(entry, "detail"), "created"), &created_time))
                            continue;
                    }
                } else continue;
            }
            if (created_time < start_time || created_time > end_time) continue;
            if (!account_key(entry_account(entry, 1), actual_black, sizeof(actual_black)) ||
                !account_key(entry_account(entry, 0), actual_white, sizeof(actual_white)) ||
                strcmp(actual_black, actual_white) == 0 ||
                !((strcmp(actual_black, black_key) == 0 && strcmp(actual_white, white_key) == 0) ||
                  (strcmp(actual_black, white_key) == 0 && strcmp(actual_white, black_key) == 0)))
                continue;
            {
                char candidate_key[512];
                make_candidate_key(entry, candidate_key, sizeof(candidate_key));
                found = -1;
                for (candidate_index = 0; candidate_index < count; candidate_index++)
                    if (strcmp(candidates[candidate_index].key, candidate_key) == 0) {
                        found = candidate_index;
                        break;
                    }
                if (found < 0) {
                    if (count >= OQ_MAX_CANDIDATES) return 0;
                    found = count++;
                    memset(&candidates[found], 0, sizeof(candidates[found]));
                    candidates[found].entry = entry;
                    strncpy(candidates[found].key, candidate_key, sizeof(candidates[found].key) - 1);
                    candidates[found].key[sizeof(candidates[found].key) - 1] = '\0';
                    candidates[found].created_millis = created_time;
                }
                candidates[found].seen_mask |= bit;
            }
        }
    }
    for (i = 0; i < count; i++) {
        long least;
        OqCandidate temporary;
        least = i;
        for (j = i + 1; j < count; j++)
            if (candidates[j].created_millis < candidates[least].created_millis) least = j;
        if (least != i) {
            temporary = candidates[i];
            candidates[i] = candidates[least];
            candidates[least] = temporary;
        }
    }
    *candidate_count = count;
    return 1;
}

static int candidate_is_already_known(const OqCandidate *candidate,
        const JsonValue *pairing, const JsonValue *round_data) {
    const char *game_id;
    const char *source_key;
    JsonValue *audit;
    JsonValue *game;
    JsonValue *pending_array;
    JsonValue *pending;
    JsonValue *candidate_array;
    JsonValue *detail;
    JsonValue *item;
    char table[128];
    char pending_table[128];
    long i;
    long j;
    int resolved;
    game_id = entry_game_id(candidate->entry);
    source_key = json_text(json_get(pairing, "sourceMessageKey"));
    if (contains_text_key(json_get(pairing, "oqGameId"), candidate->key, game_id) ||
        contains_text_key(json_get(pairing, "gameId"), candidate->key, game_id) ||
        contains_text_key(json_get(pairing, "sourceMessageKey"), candidate->key, game_id))
        return 1;
    audit = json_get(pairing, "oqAutoAudit");
    game = json_get(audit, "game");
    if (contains_text_key(json_get(audit, "candidateKey"), candidate->key, game_id) ||
        contains_text_key(json_get(game, "gameId"), candidate->key, game_id)) return 1;
    pairing_table(pairing, table, sizeof(table));
    pending_array = json_get_any(round_data, "pending", "manualPending", NULL);
    if (pending_array == NULL || pending_array->type != JSON_ARRAY) return 0;
    for (i = 0; i < pending_array->item_count; i++) {
        pending = json_at(pending_array, i);
        pairing_table(pending, pending_table, sizeof(pending_table));
        if (strcmp(table, pending_table) != 0) continue;
        resolved = json_boolean_value(json_get(pending, "resolvedByReferee"), 0) ||
            text_equals(json_text(json_get(pending, "resolutionStatus")), "resolved");
        if (!resolved) continue;
        if (contains_text_key(json_get(pending, "selectedSourceKey"), candidate->key, game_id)) return 1;
        detail = json_get(pending, "oqPendingDetail");
        candidate_array = json_get(detail, "candidates");
        if (candidate_array == NULL) candidate_array = json_get(pending, "oqCandidates");
        if (candidate_array != NULL && candidate_array->type == JSON_ARRAY) {
            for (j = 0; j < candidate_array->item_count; j++) {
                item = json_at(candidate_array, j);
                if (contains_text_key(json_get(item, "candidateKey"), candidate->key, game_id) ||
                    contains_text_key(json_get(item, "gameId"), candidate->key, game_id)) return 1;
            }
        }
    }
    (void)source_key;
    return 0;
}

static int score_oq_candidate(OqCandidate *candidate, const char *papp_black_account,
        const char *papp_white_account) {
    char actual_black[256];
    char actual_white[256];
    char papp_black[256];
    char papp_white[256];
    const char *detail_fetch_error;
    detail_fetch_error = first_json_text(candidate->entry,
        "detailFetchError", "detail_fetch_error", NULL, NULL);
    if (!score_oq_entry(candidate->entry, &candidate->replay)) {
        candidate->score_valid = 0;
        if (detail_fetch_error != NULL && *detail_fetch_error != '\0')
            snprintf(candidate->score_reason, sizeof(candidate->score_reason),
                "OQ detail fetch failed: %s", detail_fetch_error);
        else if (candidate->replay.error[0] != '\0')
            strncpy(candidate->score_reason, candidate->replay.error, sizeof(candidate->score_reason) - 1);
        else {
            const char *separator;
            separator = strchr(tournament_error, '|');
            strncpy(candidate->score_reason,
                separator == NULL ? "OQ game has no replayable position.moves" : separator + 1,
                sizeof(candidate->score_reason) - 1);
        }
        candidate->score_reason[sizeof(candidate->score_reason) - 1] = '\0';
        return 0;
    }
    if (!account_key(entry_account(candidate->entry, 1), actual_black, sizeof(actual_black)) ||
        !account_key(entry_account(candidate->entry, 0), actual_white, sizeof(actual_white)) ||
        !account_key(papp_black_account, papp_black, sizeof(papp_black)) ||
        !account_key(papp_white_account, papp_white, sizeof(papp_white)) ||
        strcmp(actual_black, actual_white) == 0 || strcmp(papp_black, papp_white) == 0 ||
        !((strcmp(papp_black, actual_black) == 0 && strcmp(papp_white, actual_white) == 0) ||
          (strcmp(papp_black, actual_white) == 0 && strcmp(papp_white, actual_black) == 0))) {
        candidate->score_valid = 0;
        strcpy(candidate->score_reason, "OQ accounts do not match both PAPP pairing accounts");
        return 0;
    }
    if (strcmp(papp_black, actual_black) == 0) {
        candidate->black_score = candidate->replay.black_score;
        candidate->white_score = candidate->replay.white_score;
    } else {
        candidate->black_score = candidate->replay.white_score;
        candidate->white_score = candidate->replay.black_score;
    }
    if (candidate->black_score < 0 || candidate->white_score < 0 ||
        candidate->black_score > 64 || candidate->white_score > 64 ||
        candidate->black_score + candidate->white_score != 64) {
        candidate->score_valid = 0;
        strcpy(candidate->score_reason, "OQ replay did not produce a valid 64-disc score pair");
        return 0;
    }
    candidate->score_valid = 1;
    if (candidate->replay.cause[0] != '\0') {
        sprintf(candidate->score_reason, "%s lost by replayed OQ terminal status %s",
            candidate->replay.black_discs < 0 ? "black" : "white",
            candidate->replay.terminal_status);
    } else {
        strcpy(candidate->score_reason,
            "replayed OQ position.moves; awarded empty squares to the board winner (normal score)");
    }
    return 1;
}

static int candidate_key_matches_array(const OqCandidate *candidate, const JsonValue *array) {
    long i;
    const char *game_id;
    JsonValue *item;
    game_id = entry_game_id(candidate->entry);
    if (array == NULL || array->type != JSON_ARRAY) return 0;
    for (i = 0; i < array->item_count; i++) {
        item = json_at(array, i);
        if (contains_text_key(item, candidate->key, game_id) ||
            contains_text_key(json_get(item, "candidateKey"), candidate->key, game_id) ||
            contains_text_key(json_get(item, "gameId"), candidate->key, game_id)) return 1;
    }
    return 0;
}

static void write_oq_account_scores(const OqCandidate *candidate,
        const char *papp_black_account, const char *papp_white_account) {
    char black_key[256];
    char white_key[256];
    account_key(papp_black_account, black_key, sizeof(black_key));
    account_key(papp_white_account, white_key, sizeof(white_key));
    putchar('{');
    write_json_string(black_key);
    printf(":%ld,", candidate->black_score);
    write_json_string(white_key);
    printf(":%ld}", candidate->white_score);
}

static void write_oq_game_summary(const OqCandidate *candidate) {
    const char *game_id;
    const char *created;
    const JsonValue *detail;
    game_id = entry_game_id(candidate->entry);
    created = entry_created_at(candidate->entry);
    printf("{\"gameId\":");
    write_nullable_string(game_id);
    printf(",\"createdAt\":");
    write_nullable_string(created);
    printf(",\"createdLocal\":");
    write_nullable_string(created);
    printf(",\"blackName\":");
    write_nullable_string(entry_account(candidate->entry, 1));
    printf(",\"whiteName\":");
    write_nullable_string(entry_account(candidate->entry, 0));
    printf(",\"status\":");
    write_nullable_string(first_json_text(candidate->entry, "status", "finalStatus", "final_status", NULL));
    detail = entry_detail(candidate->entry, 0);
    printf(",\"detail\":");
    write_json_value(detail);
    putchar('}');
}

static void write_oq_candidate_detail(OqCandidate *candidate, const JsonValue *pairing) {
    const char *game_id;
    const char *created;
    const JsonValue *detail;
    game_id = entry_game_id(candidate->entry);
    created = entry_created_at(candidate->entry);
    score_oq_candidate(candidate, pairing_text(pairing, "blackAccount", "blackOqAccount"),
        pairing_text(pairing, "whiteAccount", "whiteOqAccount"));
    printf("{\"candidateKey\":");
    write_json_string(candidate->key);
    printf(",\"gameId\":");
    write_nullable_string(game_id);
    printf(",\"createdAt\":");
    write_nullable_string(created);
    printf(",\"createdLocal\":");
    write_nullable_string(created);
    printf(",\"blackAccount\":");
    write_nullable_string(entry_account(candidate->entry, 1));
    printf(",\"whiteAccount\":");
    write_nullable_string(entry_account(candidate->entry, 0));
    printf(",\"pappBlackAccount\":");
    write_nullable_string(pairing_text(pairing, "blackAccount", "blackOqAccount"));
    printf(",\"pappWhiteAccount\":");
    write_nullable_string(pairing_text(pairing, "whiteAccount", "whiteOqAccount"));
    printf(",\"seenFromAccounts\":[");
    if (candidate->seen_mask & 1) {
        write_json_string(pairing_text(pairing, "blackAccount", "blackOqAccount"));
        if (candidate->seen_mask & 2) putchar(',');
    }
    if (candidate->seen_mask & 2)
        write_json_string(pairing_text(pairing, "whiteAccount", "whiteOqAccount"));
    printf("],\"gameDetail\":");
    detail = entry_detail(candidate->entry, 0);
    write_json_value(detail);
    if (candidate->score_valid) {
        printf(",\"blackScore\":%ld,\"whiteScore\":%ld,\"resultTime\":",
            candidate->black_score, candidate->white_score);
        write_nullable_string(created);
        printf(",\"resultSortKey\":%.0f,\"resultKind\":\"oq-auto\",\"endingKind\":\"%s\",\"scoreReason\":",
            candidate->created_millis,
            candidate->replay.cause[0] != '\0' ? candidate->replay.cause : "normal");
        write_json_string(candidate->score_reason);
        printf(",\"accountScores\":");
        write_oq_account_scores(candidate,
            pairing_text(pairing, "blackAccount", "blackOqAccount"),
            pairing_text(pairing, "whiteAccount", "whiteOqAccount"));
    } else {
        printf(",\"error\":");
        write_json_string(candidate->score_reason);
    }
    putchar('}');
}

static int table_matches(const JsonValue *item, const char *table) {
    char item_table[128];
    pairing_table(item, item_table, sizeof(item_table));
    return strcmp(item_table, table) == 0;
}

static int has_user_pending_for_table(const JsonValue *round_data, const JsonValue *pairing) {
    JsonValue *pending_array;
    JsonValue *item;
    const char *kind;
    char table[128];
    long i;
    pairing_table(pairing, table, sizeof(table));
    pending_array = json_get(round_data, "pending");
    if (pending_array != NULL && pending_array->type == JSON_ARRAY) {
        for (i = 0; i < pending_array->item_count; i++) {
            item = json_at(pending_array, i);
            if (!table_matches(item, table)) continue;
            kind = json_text(json_get(item, "pendingKind"));
            if (kind != NULL && (strncmp(kind, "user-", 5) == 0 ||
                strncmp(kind, "manual-", 7) == 0 || strcmp(kind, "user-pending") == 0))
                return 1;
        }
    }
    pending_array = json_get(round_data, "manualPending");
    if (pending_array != NULL && pending_array->type == JSON_ARRAY)
        for (i = 0; i < pending_array->item_count; i++)
            if (table_matches(json_at(pending_array, i), table)) return 1;
    return 0;
}

static int pairing_user_score_lock(const JsonValue *pairing) {
    static const char *protected_fields[] = {
        "status", "reporter", "opponent", "blackScore", "whiteScore",
        "resultText", "reason", "imagePath", "sourceMessageKey", "resultKind",
        "completedAt", "userPending"
    };
    JsonValue *edited;
    JsonValue *value;
    const char *editor;
    const char *status;
    long i;
    long black_score;
    long white_score;
    edited = json_get(pairing, "userEditedFields");
    if (edited != NULL && edited->type == JSON_OBJECT) {
        for (i = 0; i < edited->member_count; i++) {
            long j;
            for (j = 0; j < (long)(sizeof(protected_fields) / sizeof(protected_fields[0])); j++)
                if (strcmp(edited->members[i].key, protected_fields[j]) == 0 &&
                    json_boolean_value(edited->members[i].value, 0)) return 1;
        }
    }
    editor = json_text(json_get(pairing, "lastEditedBy"));
    if (editor == NULL || (strcmp(editor, "human") != 0 && strcmp(editor, "user") != 0)) return 0;
    status = json_text(json_get(pairing, "status"));
    if (status != NULL && (strcmp(status, "ready") == 0 || strcmp(status, "completed") == 0 ||
        strcmp(status, "dirty") == 0)) return 1;
    value = json_get(pairing, "sourceMessageKey");
    if ((value != NULL && json_text(value) != NULL && *json_text(value) != '\0') ||
        ((value = json_get(pairing, "resultText")) != NULL && json_text(value) != NULL && *json_text(value) != '\0') ||
        ((value = json_get(pairing, "reason")) != NULL && json_text(value) != NULL && *json_text(value) != '\0'))
        return 1;
    return read_pairing_score(pairing, &black_score, &white_score);
}

static int candidate_list_contains(const OqCandidate *candidate, const JsonValue *pairing,
        const JsonValue *round_data) {
    if (candidate_is_already_known(candidate, pairing, round_data)) return 1;
    return 0;
}

static void write_skipped_pairing(const JsonValue *pairing, const char *reason) {
    printf("{\"id\":");
    write_nullable_string(pairing_text(pairing, "id", "pairingId"));
    printf(",\"pairingId\":");
    write_nullable_string(pairing_text(pairing, "id", "pairingId"));
    printf(",\"table\":");
    write_json_value(json_get(pairing, "table"));
    printf(",\"pendingTable\":");
    write_json_value(json_get(pairing, "table"));
    printf(",\"black\":");
    write_nullable_string(pairing_text(pairing, "black", "blackName"));
    printf(",\"white\":");
    write_nullable_string(pairing_text(pairing, "white", "whiteName"));
    printf(",\"blackAccount\":");
    write_nullable_string(pairing_text(pairing, "blackAccount", "blackOqAccount"));
    printf(",\"whiteAccount\":");
    write_nullable_string(pairing_text(pairing, "whiteAccount", "whiteOqAccount"));
    printf(",\"oqGameId\":");
    write_nullable_string(pairing_text(pairing, "oqGameId", "gameId"));
    printf(",\"reason\":");
    write_json_string(reason);
    putchar('}');
}

static int pairing_status_is(const JsonValue *pairing, const char *first, const char *second) {
    const char *status;
    status = json_text(json_get(pairing, "status"));
    return status != NULL && (strcmp(status, first) == 0 ||
        (second != NULL && strcmp(status, second) == 0));
}

static void write_pending_item(long round_number, const JsonValue *pairing,
        OqCandidate *candidates, long candidate_count, const char *kind,
        const char *reason, int user_locked, const JsonValue *mismatch_pairing) {
    char table[128];
    char item_id[256];
    char source_key[256];
    char sender[256];
    char result_text[1024];
    long i;
    long shown_count;
    long mismatch_count;
    const char *prefix;
    pairing_table(pairing, table, sizeof(table));
    prefix = strcmp(kind, "oq-auto-followup") == 0 ? "oq-followup" :
        strcmp(kind, "oq-auto-score-mismatch") == 0 ? "oq-score-mismatch" : "oq-auto";
    shown_count = candidate_count > OQ_MAX_PENDING_CANDIDATES
        ? OQ_MAX_PENDING_CANDIDATES : candidate_count;
    sprintf(item_id, "%s-r%ld-t%s", prefix, round_number, table);
    sprintf(source_key, "%s-r%ld-t%s", prefix, round_number, table);
    sprintf(sender, "OQ自动查询 第%s台", table);
    sprintf(result_text, "OQ自动查询 pending：第 %s 台 %s", table, reason);
    printf("{\"id\":");
    write_json_string(item_id);
    printf(",\"pairingId\":");
    write_nullable_string(pairing_text(pairing, "id", "pairingId"));
    printf(",\"round\":%ld,\"table\":", round_number);
    write_json_value(json_get(pairing, "table"));
    printf(",\"pendingTable\":");
    write_json_value(json_get(pairing, "table"));
    printf(",\"sender\":");
    write_json_string(sender);
    printf(",\"wechatSender\":");
    write_json_string(sender);
    printf(",\"opponent\":\"\",\"black\":");
    write_nullable_string(pairing_text(pairing, "black", "blackName"));
    printf(",\"white\":");
    write_nullable_string(pairing_text(pairing, "white", "whiteName"));
    printf(",\"blackAccount\":");
    write_nullable_string(pairing_text(pairing, "blackAccount", "blackOqAccount"));
    printf(",\"whiteAccount\":");
    write_nullable_string(pairing_text(pairing, "whiteAccount", "whiteOqAccount"));
    printf(",\"oqGameId\":");
    if (candidate_count == 1) write_nullable_string(entry_game_id(candidates[0].entry));
    else write_json_string("");
    printf(",\"verdict\":");
    write_json_string(kind);
    printf(",\"pendingKind\":");
    write_json_string(kind);
    printf(",\"resultText\":");
    write_json_string(result_text);
    printf(",\"reason\":");
    write_json_string(reason);
    printf(",\"accountMismatchText\":");
    write_json_string("OQ 对局匹配待核对");
    printf(",\"oqCandidates\":[");
    for (i = 0; i < shown_count; i++) {
        if (i) putchar(',');
        write_oq_candidate_detail(&candidates[i], pairing);
    }
    printf("],\"oqPendingDetail\":{\"table\":");
    write_json_value(json_get(pairing, "table"));
    printf(",\"black\":");
    write_nullable_string(pairing_text(pairing, "black", "blackName"));
    printf(",\"white\":");
    write_nullable_string(pairing_text(pairing, "white", "whiteName"));
    printf(",\"blackAccount\":");
    write_nullable_string(pairing_text(pairing, "blackAccount", "blackOqAccount"));
    printf(",\"whiteAccount\":");
    write_nullable_string(pairing_text(pairing, "whiteAccount", "whiteOqAccount"));
    printf(",\"userEdited\":%s,\"candidateCount\":%ld,\"candidates\":[",
        user_locked ? "true" : "false", candidate_count);
    for (i = 0; i < shown_count; i++) {
        if (i) putchar(',');
        write_oq_candidate_detail(&candidates[i], pairing);
    }
    printf("]},\"reviewAction\":\"核对候选 OQ 对局；确认后处理 pending。\",\"sourceMessageKey\":");
    write_json_string(source_key);
    printf(",\"resultSource\":\"oq-auto\",\"lastEditedBy\":\"script\"");
    if (strcmp(kind, "oq-auto-score-mismatch") == 0 && mismatch_pairing != NULL) {
        long current_black;
        long current_white;
        if (read_pairing_score(mismatch_pairing, &current_black, &current_white)) {
            printf(",\"oqScoreMismatch\":[");
            mismatch_count = 0;
            for (i = 0; i < candidate_count; i++) {
                if (!candidates[i].score_valid ||
                    (candidates[i].black_score == current_black &&
                     candidates[i].white_score == current_white)) continue;
                if (mismatch_count++) putchar(',');
                printf("{\"currentBlackScore\":%ld,\"currentWhiteScore\":%ld,\"oqBlackScore\":%ld,\"oqWhiteScore\":%ld,\"scoreReason\":",
                    current_black, current_white, candidates[i].black_score, candidates[i].white_score);
                write_json_string(candidates[i].score_reason);
                printf(",\"gameId\":");
                write_nullable_string(entry_game_id(candidates[i].entry));
                printf(",\"createdLocal\":");
                write_nullable_string(entry_created_at(candidates[i].entry));
                putchar('}');
            }
            putchar(']');
        }
    }
    putchar('}');
}

static void write_ready_pairing(const JsonValue *pairing, OqCandidate *candidate,
        long round_number) {
    static const char *replace_fields[] = {
        "status", "blackScore", "whiteScore", "oqGameId", "reporter", "opponent",
        "resultKind", "resultSource", "sourceLocalId", "imagePath",
        "resultText", "reason", "sourceMessageKey", "resultTime", "resultSortKey",
        "completedAt", "updatedAt", "lastEditedBy", "oqAutoAudit", "oqUpdatedAt",
        "pappReadbackAt"
    };
    long i;
    int first;
    const char *game_id;
    const char *created;
    const char *black_account;
    const char *white_account;
    const char *ending;
    game_id = entry_game_id(candidate->entry);
    created = entry_created_at(candidate->entry);
    black_account = pairing_text(pairing, "blackAccount", "blackOqAccount");
    white_account = pairing_text(pairing, "whiteAccount", "whiteOqAccount");
    ending = candidate->replay.cause[0] == '\0' ? "normal" : candidate->replay.cause;
    first = 1;
    putchar('{');
    if (pairing != NULL && pairing->type == JSON_OBJECT) {
        for (i = 0; i < pairing->member_count; i++) {
            long j;
            int replaced;
            replaced = 0;
            for (j = 0; j < (long)(sizeof(replace_fields) / sizeof(replace_fields[0])); j++)
                if (strcmp(pairing->members[i].key, replace_fields[j]) == 0) replaced = 1;
            if (replaced) continue;
            if (!first) putchar(',');
            first = 0;
            write_json_string(pairing->members[i].key);
            putchar(':');
            write_json_value(pairing->members[i].value);
        }
    }
#define OQ_FIELD(name, value_writer) do { if (!first) putchar(','); first = 0; write_json_string(name); putchar(':'); value_writer; } while (0)
    OQ_FIELD("status", write_json_string("ready"));
    OQ_FIELD("blackScore", printf("%ld", candidate->black_score));
    OQ_FIELD("whiteScore", printf("%ld", candidate->white_score));
    OQ_FIELD("oqGameId", write_nullable_string(game_id));
    OQ_FIELD("reporter", write_json_string("OQ自动查询"));
    OQ_FIELD("opponent", write_json_string(""));
    OQ_FIELD("resultKind", write_json_string("oq-auto"));
    OQ_FIELD("resultSource", write_json_string("oq-auto"));
    OQ_FIELD("sourceLocalId", write_json_string(""));
    OQ_FIELD("imagePath", write_json_string(""));
    {
        char result_text[1024];
        sprintf(result_text, "OQ自动查询：%s %s vs %s => %ld-%ld",
            created == NULL ? "" : created,
            black_account == NULL ? "" : black_account,
            white_account == NULL ? "" : white_account,
            candidate->black_score, candidate->white_score);
        OQ_FIELD("resultText", write_json_string(result_text));
    }
    OQ_FIELD("reason", write_json_string(candidate->score_reason));
    {
        char source_message_key[600];
        sprintf(source_message_key, "oq-auto:%s", candidate->key);
        OQ_FIELD("sourceMessageKey", write_json_string(source_message_key));
    }
    OQ_FIELD("resultTime", write_nullable_string(created));
    OQ_FIELD("resultSortKey", printf("%.0f", candidate->created_millis));
    OQ_FIELD("completedAt", printf("null"));
    OQ_FIELD("updatedAt", printf("%ld", (long)time(NULL) * 1000L));
    OQ_FIELD("lastEditedBy", write_json_string("script"));
    OQ_FIELD("oqUpdatedAt", write_json_string(""));
    OQ_FIELD("pappReadbackAt", write_json_string(""));
    OQ_FIELD("oqAutoAudit", printf("{\"by\":\"script\",\"mode\":\"SCORE\",\"game\":");
        write_oq_game_summary(candidate);
        printf(",\"accountScores\":");
        write_oq_account_scores(candidate, black_account, white_account);
        printf(",\"pappBlackAccount\":");
        write_nullable_string(black_account);
        printf(",\"pappWhiteAccount\":");
        write_nullable_string(white_account);
        printf(",\"seenFromAccounts\":[");
        if (candidate->seen_mask & 1) {
            write_json_string(black_account);
            if (candidate->seen_mask & 2) putchar(',');
        }
        if (candidate->seen_mask & 2) write_json_string(white_account);
        printf("],\"scoreRule\":");
        write_json_string(candidate->score_reason);
        printf(",\"userEditedFieldsChecked\":[],\"replay\":{\"blackDiscs\":%ld,\"whiteDiscs\":%ld,\"emptySquares\":%ld,\"playedMoves\":%ld,\"explicitPasses\":%ld,\"endingKind\":\"%s\"}}",
            candidate->replay.black_discs < 0 ? 0 : candidate->replay.black_discs,
            candidate->replay.white_discs < 0 ? 0 : candidate->replay.white_discs,
            candidate->replay.empty_squares, candidate->replay.played_moves,
            candidate->replay.explicit_passes, ending));
#undef OQ_FIELD
    putchar('}');
    (void)round_number;
}

static void utc_now_text(char *buffer, size_t capacity) {
    time_t now;
    struct tm *parts;
    now = time(NULL);
    parts = gmtime(&now);
    if (parts == NULL || capacity == 0) {
        if (capacity > 0) buffer[0] = '\0';
        return;
    }
    strftime(buffer, capacity, "%Y-%m-%dT%H:%M:%SZ", parts);
}

static void write_user_followup(const JsonValue *existing, const JsonValue *pairing,
        OqCandidate *candidates, long candidate_count, const char *reason,
        const char *timestamp) {
    JsonValue *old_followup;
    JsonValue *old_history;
    long i;
    long shown_count;
    int first;
    shown_count = candidate_count > OQ_MAX_PENDING_CANDIDATES
        ? OQ_MAX_PENDING_CANDIDATES : candidate_count;
    first = 1;
    putchar('{');
    if (existing != NULL && existing->type == JSON_OBJECT) {
        for (i = 0; i < existing->member_count; i++) {
            if (!first) putchar(',');
            first = 0;
            write_json_string(existing->members[i].key);
            putchar(':');
            write_json_value(existing->members[i].value);
        }
    }
    printf(",\"oqFollowupDetected\":true,\"oqFollowupAt\":");
    write_json_string(timestamp);
    printf(",\"oqFollowupReason\":");
    write_json_string(reason);
    printf(",\"oqFollowupCandidates\":[");
    for (i = 0; i < shown_count; i++) {
        if (i) putchar(',');
        write_oq_candidate_detail(&candidates[i], pairing);
    }
    printf("],\"oqCandidates\":[");
    for (i = 0; i < shown_count; i++) {
        if (i) putchar(',');
        write_oq_candidate_detail(&candidates[i], pairing);
    }
    printf("],\"oqFollowup\":{\"detectedAt\":");
    write_json_string(timestamp);
    printf(",\"reason\":");
    write_json_string(reason);
    printf(",\"readySnapshot\":");
    write_json_value(pairing);
    printf(",\"candidates\":[");
    for (i = 0; i < shown_count; i++) {
        if (i) putchar(',');
        write_oq_candidate_detail(&candidates[i], pairing);
    }
    printf("],\"history\":[");
    old_followup = json_get(existing, "oqFollowup");
    old_history = json_get(old_followup, "history");
    first = 1;
    if (old_history != NULL && old_history->type == JSON_ARRAY) {
        for (i = 0; i < old_history->item_count; i++) {
            if (!first) putchar(',');
            first = 0;
            write_json_value(json_at(old_history, i));
        }
    }
    if (!first) putchar(',');
    printf("{\"detectedAt\":");
    write_json_string(timestamp);
    printf(",\"reason\":");
    write_json_string(reason);
    printf(",\"readySnapshot\":");
    write_json_value(pairing);
    printf(",\"candidates\":[");
    for (i = 0; i < shown_count; i++) {
        if (i) putchar(',');
        write_oq_candidate_detail(&candidates[i], pairing);
    }
    printf("]}]}}");
}

static JsonValue *find_user_pending(const JsonValue *round_data,
        const JsonValue *pairing) {
    JsonValue *arrays[2];
    JsonValue *item;
    const char *kind;
    char table[128];
    long a;
    long i;
    pairing_table(pairing, table, sizeof(table));
    arrays[0] = json_get(round_data, "pending");
    arrays[1] = json_get(round_data, "manualPending");
    for (a = 0; a < 2; a++) {
        if (arrays[a] == NULL || arrays[a]->type != JSON_ARRAY) continue;
        for (i = 0; i < arrays[a]->item_count; i++) {
            item = json_at(arrays[a], i);
            if (!table_matches(item, table)) continue;
            kind = json_text(json_get(item, "pendingKind"));
            if ((kind != NULL && (strncmp(kind, "user-", 5) == 0 ||
                 strncmp(kind, "manual-", 7) == 0 || strcmp(kind, "user-pending") == 0)) ||
                a == 1) return item;
        }
    }
    return NULL;
}

static void write_game_available(const JsonValue *pairing, OqCandidate *candidate,
        const char *editor) {
    const char *black_account;
    const char *white_account;
    black_account = pairing_text(pairing, "blackAccount", "blackOqAccount");
    white_account = pairing_text(pairing, "whiteAccount", "whiteOqAccount");
    printf("{\"id\":");
    write_nullable_string(pairing_text(pairing, "id", "pairingId"));
    printf(",\"pairingId\":");
    write_nullable_string(pairing_text(pairing, "id", "pairingId"));
    printf(",\"table\":");
    write_json_value(json_get(pairing, "table"));
    printf(",\"black\":");
    write_nullable_string(pairing_text(pairing, "black", "blackName"));
    printf(",\"white\":");
    write_nullable_string(pairing_text(pairing, "white", "whiteName"));
    printf(",\"oqGameAvailable\":true,\"oqGameAvailableAudit\":{\"by\":\"script\",\"verifiedExistingEditor\":");
    write_json_string(editor);
    printf(",\"game\":");
    write_oq_game_summary(candidate);
    printf(",\"candidateKey\":");
    write_json_string(candidate->key);
    printf(",\"accountScores\":");
    write_oq_account_scores(candidate, black_account, white_account);
    printf(",\"pappBlackAccount\":");
    write_nullable_string(black_account);
    printf(",\"pappWhiteAccount\":");
    write_nullable_string(white_account);
    printf(",\"seenFromAccounts\":[");
    if (candidate->seen_mask & 1) {
        write_json_string(black_account);
        if (candidate->seen_mask & 2) putchar(',');
    }
    if (candidate->seen_mask & 2) write_json_string(white_account);
    printf("],\"scoreRule\":");
    write_json_string(candidate->score_reason);
    printf(",\"verifiedBlackScore\":%ld,\"verifiedWhiteScore\":%ld,\"matchingCandidateCount\":1},\"source\":\"oq-game-available\"}",
        candidate->black_score, candidate->white_score);
}

enum OqDecisionKind { OQ_READY, OQ_PENDING, OQ_SKIPPED, OQ_AVAILABLE, OQ_USER_FOLLOWUP };

typedef struct {
    const JsonValue *pairing;
    OqCandidate *candidates;
    long candidate_count;
    const JsonValue *existing_pending;
    const JsonValue *mismatch_pairing;
    char status[32];
    char editor[96];
} OqWork;

typedef struct {
    OqWork *work;
    enum OqDecisionKind kind;
    const JsonValue *mismatch_pairing;
    char pending_kind[64];
    char reason[512];
    int user_locked;
} OqDecision;

static void add_oq_decision(OqDecision *items, long *count, OqWork *work,
        enum OqDecisionKind kind, const char *pending_kind, const char *reason,
        int user_locked, const JsonValue *mismatch_pairing) {
    OqDecision *item;
    if (*count >= TOURNAMENT_MAX_PLAYERS) return;
    item = &items[(*count)++];
    memset(item, 0, sizeof(*item));
    item->work = work;
    item->kind = kind;
    item->mismatch_pairing = mismatch_pairing;
    item->user_locked = user_locked;
    if (pending_kind != NULL) {
        strncpy(item->pending_kind, pending_kind, sizeof(item->pending_kind) - 1);
        item->pending_kind[sizeof(item->pending_kind) - 1] = '\0';
    }
    if (reason != NULL) {
        strncpy(item->reason, reason, sizeof(item->reason) - 1);
        item->reason[sizeof(item->reason) - 1] = '\0';
    }
}

static void write_oq_detail_requests(OqWork *works, long work_count) {
    long i;
    long j;
    long shown_count;
    int first;
    const char *game_id;
    char table[128];
    first = 1;
    for (i = 0; i < work_count; i++) {
        OqWork *work;
        work = &works[i];
        shown_count = work->candidate_count > OQ_MAX_PENDING_CANDIDATES
            ? OQ_MAX_PENDING_CANDIDATES : work->candidate_count;
        pairing_table(work->pairing, table, sizeof(table));
        for (j = 0; j < shown_count; j++) {
            OqCandidate *candidate;
            candidate = &work->candidates[j];
            if (entry_detail(candidate->entry, 0) != NULL) continue;
            game_id = entry_game_id(candidate->entry);
            if (game_id == NULL || *game_id == '\0') continue;
            if (!first) putchar(',');
            first = 0;
            printf("{\"gameId\":");
            write_json_string(game_id);
            printf(",\"candidateKey\":");
            write_json_string(candidate->key);
            printf(",\"table\":");
            write_json_value(json_get(work->pairing, "table"));
            putchar('}');
        }
    }
}

static int operation_oq_poll(const JsonValue *root) {
    JsonValue *round_data;
    JsonValue *pairings;
    JsonValue *games_json;
    JsonValue *errors;
    JsonValue *round_start_json;
    JsonValue *round_end_json;
    JsonValue *window_json;
    OqWork works[TOURNAMENT_MAX_PLAYERS];
    OqDecision *ready;
    OqDecision *pending;
    OqDecision *skipped;
    OqDecision *available;
    char black_key[256];
    char white_key[256];
    char timestamp[64];
    char abnormality[384];
    const char *black_account;
    const char *white_account;
    const char *status;
    const char *editor;
    double start_time;
    double end_time;
    double parsed_window;
    long window_minutes;
    long round_number;
    long work_count;
    long candidate_count;
    long filtered_count;
    long ready_count;
    long pending_count;
    long skipped_count;
    long available_count;
    long i;
    long j;
    int has_snapshot;
    int user_locked;
    int has_mismatch;
    OqWork *work;

    round_data = json_get(root, "roundData");
    pairings = json_get(root, "pairings");
    if (pairings == NULL) pairings = json_get(round_data, "pairings");
    if (pairings == NULL || pairings->type != JSON_ARRAY) pairings = NULL;
    games_json = json_get(root, "gamesByAccount");
    errors = json_get_any(root, "queryErrors", "errors", NULL);
    round_start_json = json_get(root, "roundStartAt");
    if (round_start_json == NULL) round_start_json = json_get(round_data, "roundStartAt");
    if (!parse_date_millis(round_start_json, &start_time)) {
        set_tournament_error("round-start-invalid", "OQ 查询需要有效的本轮开始时间");
        return 0;
    }
    window_json = json_get(root, "windowMinutes");
    if (window_json == NULL) window_json = json_get(round_data, "windowMinutes");
    if (!json_double_value(window_json, &parsed_window) || parsed_window <= 0.0) parsed_window = 40.0;
    window_minutes = (long)parsed_window;
    if (window_minutes < 1) window_minutes = 1;
    round_end_json = json_get(root, "roundEndAt");
    if (round_end_json == NULL) round_end_json = json_get(round_data, "roundEndAt");
    if (round_end_json == NULL) round_end_json = json_get(json_get(root, "window"), "endLocal");
    if (!parse_date_millis(round_end_json, &end_time))
        end_time = start_time + (double)window_minutes * 60000.0;
    if (!json_long_value(json_get(root, "round"), &round_number) || round_number < 1)
        round_number = 1;
    has_snapshot = games_json != NULL && games_json->type == JSON_OBJECT;
    utc_now_text(timestamp, sizeof(timestamp));
    work_count = ready_count = pending_count = skipped_count = available_count = 0;
    ready = (OqDecision *)calloc(TOURNAMENT_MAX_PLAYERS, sizeof(OqDecision));
    pending = (OqDecision *)calloc(TOURNAMENT_MAX_PLAYERS, sizeof(OqDecision));
    skipped = (OqDecision *)calloc(TOURNAMENT_MAX_PLAYERS, sizeof(OqDecision));
    available = (OqDecision *)calloc(TOURNAMENT_MAX_PLAYERS, sizeof(OqDecision));
    if (ready == NULL || pending == NULL || skipped == NULL || available == NULL) {
        free(ready);
        free(pending);
        free(skipped);
        free(available);
        set_tournament_error("out-of-memory", "PAPP C OQ result buffer allocation failed");
        return 0;
    }
    if (pairings != NULL && pairings->item_count > TOURNAMENT_MAX_PLAYERS) {
        free(ready); free(pending); free(skipped); free(available);
        set_tournament_error("too-many-pairings", "OQ 每轮配对超过 PAPP C 选手数上限");
        return 0;
    }
    if (pairings != NULL) {
        for (i = 0; i < pairings->item_count; i++) {
            work = &works[work_count++];
            memset(work, 0, sizeof(*work));
            work->pairing = json_at(pairings, i);
            status = json_text(json_get(work->pairing, "status"));
            if (status == NULL) status = "imported";
            strncpy(work->status, status, sizeof(work->status) - 1);
            work->status[sizeof(work->status) - 1] = '\0';
            editor = json_text(json_get(work->pairing, "lastEditedBy"));
            if (editor != NULL) {
                strncpy(work->editor, editor, sizeof(work->editor) - 1);
                work->editor[sizeof(work->editor) - 1] = '\0';
            }
            if (strcmp(status, "bye") == 0) {
                add_oq_decision(skipped, &skipped_count, work, OQ_SKIPPED,
                    NULL, "bye", 0, NULL);
                continue;
            }
            work->candidates = (OqCandidate *)calloc(OQ_MAX_CANDIDATES, sizeof(OqCandidate));
            if (work->candidates == NULL) {
                set_tournament_error("out-of-memory", "PAPP C OQ candidate buffer allocation failed");
                goto oq_fail;
            }
            black_account = pairing_text(work->pairing, "blackAccount", "blackOqAccount");
            white_account = pairing_text(work->pairing, "whiteAccount", "whiteOqAccount");
            if (!account_key(black_account, black_key, sizeof(black_key)) ||
                !account_key(white_account, white_key, sizeof(white_key)) ||
                strcmp(black_key, white_key) == 0) {
                if (strcmp(status, "ready") == 0 || strcmp(status, "completed") == 0 ||
                    strcmp(status, "dirty") == 0) {
                    add_oq_decision(skipped, &skipped_count, work, OQ_SKIPPED,
                        NULL, "pairing account mapping is incomplete", 0, NULL);
                } else {
                    add_oq_decision(pending, &pending_count, work, OQ_PENDING,
                        "oq-auto-unmapped-account",
                        "配对缺少唯一、有效的 OQ 账号映射，无法安全匹配对局", 0, NULL);
                }
                continue;
            }
            candidate_count = 0;
            if (has_snapshot && !collect_oq_candidates(games_json, black_account, white_account,
                    start_time, end_time, work->candidates, &candidate_count)) {
                set_tournament_error("too-many-oq-candidates", "OQ 单桌候选对局超过 1024 条上限");
                goto oq_fail;
            }
            filtered_count = 0;
            user_locked = has_user_pending_for_table(round_data, work->pairing);
            for (j = 0; j < candidate_count; j++) {
                /* Only follow-up checks exclude known games. A stored transcript
                   does not mean an unregistered pairing has been scored. */
                if ((user_locked || strcmp(status, "ready") == 0 ||
                     strcmp(status, "completed") == 0 || strcmp(status, "dirty") == 0 ||
                     json_boolean_value(json_get(work->pairing, "dirty"), 0) ||
                     pairing_user_score_lock(work->pairing)) &&
                    candidate_list_contains(&work->candidates[j], work->pairing, round_data)) continue;
                if (filtered_count != j) work->candidates[filtered_count] = work->candidates[j];
                filtered_count++;
            }
            work->candidate_count = filtered_count;
            for (j = 0; j < work->candidate_count; j++)
                score_oq_candidate(&work->candidates[j], black_account, white_account);
            if (user_locked) {
                if (work->candidate_count >= 2) {
                    work->existing_pending = find_user_pending(round_data, work->pairing);
                    utc_now_text(timestamp, sizeof(timestamp));
                    add_oq_decision(pending, &pending_count, work, OQ_USER_FOLLOWUP,
                        "user-pending",
                        "该桌已有用户 pending，OQ 自动查询发现同双方账号对局；未覆盖用户原因或比分",
                        1, NULL);
                }
                add_oq_decision(skipped, &skipped_count, work, OQ_SKIPPED,
                    NULL, "user-pending", 0, NULL);
                continue;
            }
            if (strcmp(status, "ready") == 0 || strcmp(status, "completed") == 0) {
                if ((strcmp(work->editor, "agent") == 0 || strcmp(work->editor, "human") == 0 ||
                     strcmp(work->editor, "user") == 0) && work->candidate_count > 0) {
                    long current_black;
                    long current_white;
                    has_mismatch = 0;
                    if (read_pairing_score(work->pairing, &current_black, &current_white))
                        for (j = 0; j < work->candidate_count; j++)
                            if (work->candidates[j].score_valid &&
                                (work->candidates[j].black_score != current_black ||
                                 work->candidates[j].white_score != current_white)) has_mismatch = 1;
                    if (has_mismatch) {
                        add_oq_decision(pending, &pending_count, work, OQ_PENDING,
                            "oq-auto-score-mismatch",
                            "该桌已由用户登记，但 OQ 棋谱回放比分与当前登记不一致",
                            1, work->pairing);
                    } else if (work->candidate_count >= 2) {
                        add_oq_decision(pending, &pending_count, work, OQ_PENDING,
                            "oq-auto-followup",
                            "该桌已登记，OQ 又发现多局同双方账号对局；当前比分未覆盖，请核对",
                            1, NULL);
                    } else if (work->candidate_count == 1 && work->candidates[0].score_valid) {
                        add_oq_decision(available, &available_count, work, OQ_AVAILABLE,
                            NULL, NULL, 0, NULL);
                    }
                }
                add_oq_decision(skipped, &skipped_count, work, OQ_SKIPPED,
                    NULL, "already completed", 0, NULL);
                continue;
            }
            if (strcmp(status, "dirty") == 0 ||
                json_boolean_value(json_get(work->pairing, "dirty"), 0)) {
                if (work->candidate_count >= 2)
                    add_oq_decision(pending, &pending_count, work, OQ_PENDING,
                        "oq-auto-followup",
                        "该桌是旧 dirty 状态，OQ 自动查询发现同双方账号对局；未覆盖比分",
                        1, NULL);
                add_oq_decision(skipped, &skipped_count, work, OQ_SKIPPED,
                    NULL, "dirty row", 0, NULL);
                continue;
            }
            if (pairing_user_score_lock(work->pairing)) {
                if (work->candidate_count >= 2)
                    add_oq_decision(pending, &pending_count, work, OQ_PENDING,
                        "oq-auto-followup",
                        "该桌已有用户编辑锁，OQ 自动查询发现同双方账号对局；未覆盖用户编辑",
                        1, NULL);
                add_oq_decision(skipped, &skipped_count, work, OQ_SKIPPED,
                    NULL, "user-edited", 0, NULL);
                continue;
            }
            if (!has_snapshot) {
                add_oq_decision(pending, &pending_count, work, OQ_PENDING,
                    "oq-auto", "OQ 尚未返回稳定结果", 0, NULL);
                continue;
            }
            if (work->candidate_count == 0) {
                add_oq_decision(skipped, &skipped_count, work, OQ_SKIPPED,
                    NULL, "no matching OQ game", 0, NULL);
                continue;
            }
            if (work->candidate_count >= 2) {
                add_oq_decision(pending, &pending_count, work, OQ_PENDING,
                    "oq-auto-multiple-games",
                    "同一时间窗口内命中多局完全相同双方账号", 0, NULL);
                continue;
            }
            if (!work->candidates[0].score_valid) {
                sprintf(abnormality, "OQ 对局命中但无法安全计算比分：%s",
                    work->candidates[0].score_reason);
                add_oq_decision(pending, &pending_count, work, OQ_PENDING,
                    "oq-auto-abnormality", abnormality, 0, NULL);
                continue;
            }
            add_oq_decision(ready, &ready_count, work, OQ_READY, NULL, NULL, 0, NULL);
        }
    }
    printf("{\"ok\":true,\"source\":\"papp-c\",\"operation\":\"oq-poll\",\"round\":%ld,\"ready\":[", round_number);
    for (i = 0; i < ready_count; i++) {
        if (i) putchar(',');
        write_ready_pairing(ready[i].work->pairing, &ready[i].work->candidates[0], round_number);
    }
    printf("],\"pending\":[");
    for (i = 0; i < pending_count; i++) {
        OqDecision *item;
        item = &pending[i];
        if (i) putchar(',');
        if (item->kind == OQ_USER_FOLLOWUP) {
            write_user_followup(item->work->existing_pending, item->work->pairing,
                item->work->candidates, item->work->candidate_count, item->reason, timestamp);
        } else {
            write_pending_item(round_number, item->work->pairing,
                item->work->candidates, item->work->candidate_count,
                item->pending_kind, item->reason, item->user_locked,
                item->mismatch_pairing);
        }
    }
    printf("],\"skipped\":[");
    for (i = 0; i < skipped_count; i++) {
        if (i) putchar(',');
        write_skipped_pairing(skipped[i].work->pairing, skipped[i].reason);
    }
    printf("],\"gameAvailable\":[");
    for (i = 0; i < available_count; i++) {
        if (i) putchar(',');
        write_game_available(available[i].work->pairing,
            &available[i].work->candidates[0], available[i].work->editor);
    }
    printf("],\"detailRequests\":[");
    write_oq_detail_requests(works, work_count);
    printf("],\"queryErrors\":");
    write_json_value(errors);
    printf(",\"window\":{\"startLocal\":");
    write_nullable_string(json_text(round_start_json));
    printf(",\"endLocal\":");
    if (round_end_json != NULL) write_nullable_string(json_text(round_end_json));
    else write_nullable_string(json_text(round_start_json));
    printf(",\"minutes\":%ld}}\n", window_minutes);
    for (i = 0; i < work_count; i++) free(works[i].candidates);
    free(ready); free(pending); free(skipped); free(available);
    return 1;

oq_fail:
    for (i = 0; i < work_count; i++) free(works[i].candidates);
    free(ready); free(pending); free(skipped); free(available);
    return 0;
}

static char *read_request(void) {
    char *buffer;
    char *resized;
    size_t capacity;
    size_t length;
    size_t read_count;
    capacity = 8192;
    length = 0;
    buffer = (char *)malloc(capacity + 1);
    if (buffer == NULL) return NULL;
    for (;;) {
        if (length == capacity) {
            if ((long)capacity >= JSON_INPUT_LIMIT) {
                free(buffer);
                return NULL;
            }
            capacity *= 2;
            if ((long)capacity > JSON_INPUT_LIMIT) capacity = (size_t)JSON_INPUT_LIMIT;
            resized = (char *)realloc(buffer, capacity + 1);
            if (resized == NULL) {
                free(buffer);
                return NULL;
            }
            buffer = resized;
        }
        read_count = fread(buffer + length, 1, capacity - length, stdin);
        length += read_count;
        if (read_count == 0) break;
    }
    buffer[length] = '\0';
    return buffer;
}

int papp_tournament_json_main(void) {
    char *input;
    JsonValue *root;
    JsonValue *operation_json;
    const char *operation;
    const char *parse_error;
    int ok;
    tournament_error[0] = '\0';
    input = read_request();
    if (input == NULL) {
        set_tournament_error("request-too-large", "PAPP C JSON 请求为空、过大或内存不足");
        write_error_response();
        return 0;
    }
    root = parse_json_document(input, strlen(input), &parse_error);
    if (root == NULL || root->type != JSON_OBJECT) {
        set_tournament_error("invalid-json", parse_error == NULL ? "请求必须是 JSON 对象" : parse_error);
        write_error_response();
        free(input);
        return 0;
    }
    operation_json = json_get(root, "operation");
    operation = json_text(operation_json);
    if (operation == NULL) {
        set_tournament_error("missing-operation", "JSON 请求缺少 operation");
        write_error_response();
        free(input);
        return 0;
    }
    if (strcmp(operation, "round-count") == 0) ok = operation_round_count(root);
    else if (strcmp(operation, "validate-score") == 0) ok = operation_validate_score(root);
    else if (strcmp(operation, "round-standings") == 0) ok = operation_round_standings(root);
    else if (strcmp(operation, "preliminary-standings") == 0) ok = operation_standings(root, 0);
    else if (strcmp(operation, "overall-standings") == 0) ok = operation_standings(root, 1);
    else if (strcmp(operation, "stage-status") == 0) ok = operation_stage_status(root);
    else if (strcmp(operation, "pairings") == 0) ok = operation_pairings(root);
    else if (strcmp(operation, "validate-pairings") == 0) ok = operation_validate_pairings(root);
    else if (strcmp(operation, "write-score-batch") == 0) ok = operation_score_batch(root, 0);
    else if (strcmp(operation, "read-score-batch") == 0) ok = operation_score_batch(root, 1);
    else if (strcmp(operation, "oq-poll") == 0) ok = operation_oq_poll(root);
    else {
        set_tournament_error("unknown-operation", "PAPP C 不支持此 tournament operation");
        ok = 0;
    }
    if (!ok) write_error_response();
    free(input);
    return 0;
}
